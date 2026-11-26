"""
Tasks and follow-ups on leads (T-3020, FR-CRM-004, ADR-057).

Who: staff of the lead's organization (owner, org admin, team or sales manager, CRM
operator) see and manage every task there; an agent sees tasks on their own leads
and tasks given to them, and creates tasks only on their own leads, for themselves.
Platform admins see everything. Membership decides (ADR-020), not ``user.role``.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Lead, LeadActivity, LeadTask

logger = logging.getLogger(__name__)

T = LeadTask.Status
STAFF = ('owner', 'org_admin', 'team_manager', 'sales_manager', 'crm_operator')
AGENTS = ('agent', 'freelance_agent')
HORIZON = timedelta(days=366)
GRACE = timedelta(minutes=5)          # a due time "now" typed a moment ago is not in the past
REDACTED = '[REDACTED]'


class TaskError(Exception):
    def __init__(self, message: str, field: str = 'detail'):
        super().__init__(message)
        self.field = field


def _actor(user):
    """(organization, staff?, agent) of the caller's active membership."""
    from apps.core.permissions import active_org_role, get_user_org
    org, role = get_user_org(user), active_org_role(user)
    agent = getattr(user, 'agent_profile', None) if role in AGENTS else None
    return org, role in STAFF, agent


def visible(user):
    qs = LeadTask.objects.select_related('lead__user', 'assignee', 'organization')
    if user.role == 'admin':
        return qs
    org, staff, agent = _actor(user)
    if org is None:
        return qs.none()
    qs = qs.filter(organization=org)
    if staff:
        return qs
    if agent is not None:
        return qs.filter(Q(assignee=agent) | Q(lead__assigned_agent=agent))
    return qs.none()


def may_edit(user, task: LeadTask) -> bool:
    """Staff, the assignee, or the lead's agent. Visibility is checked before."""
    if user.role == 'admin':
        return True
    org, staff, agent = _actor(user)
    if org is None or org.pk != task.organization_id:
        return False
    return staff or (agent is not None and agent.pk in (task.assignee_id, task.lead.assigned_agent_id))


def _agents(org):
    from apps.reports.performance import agents_of
    return agents_of(org).filter(is_active=True, user__is_active=True)


def _check_times(due_at, remind_at, now):
    if due_at < now - GRACE:
        raise TaskError('The due time is in the past.', 'due_at')
    if due_at > now + HORIZON:
        raise TaskError('At most a year ahead.', 'due_at')
    if remind_at > due_at:
        raise TaskError('The reminder must not be after the due time.', 'remind_at')


def create(user, lead: Lead, *, title: str, due_at, remind_at=None, notes: str = '', kind: str = LeadTask.Kind.CALL,
           assignee=None) -> LeadTask:
    if lead.organization_id is None:
        raise TaskError('Tasks need a lead of an organization.', 'organization')
    if user.role == 'admin':
        org, staff, agent = lead.organization, True, None
    else:
        org, staff, agent = _actor(user)
        if org is None or org.pk != lead.organization_id or not (
                staff or (agent is not None and lead.assigned_agent_id == agent.pk)):
            raise TaskError('Lead not found.', 'lead')
    if assignee is None:
        assignee = agent or lead.assigned_agent
    if not staff and (agent is None or assignee is None or assignee.pk != agent.pk):
        raise TaskError('Agents create tasks for themselves.', 'assignee')
    if assignee is not None and not _agents(lead.organization).filter(pk=assignee.pk).exists():
        raise TaskError('The assignee must be an active agent of this organization.', 'assignee')
    remind_at = remind_at or due_at
    _check_times(due_at, remind_at, timezone.now())
    with transaction.atomic():
        task = LeadTask.objects.create(lead=lead, organization_id=lead.organization_id, assignee=assignee,
                                       title=title.strip()[:200], notes=notes, kind=kind, due_at=due_at,
                                       remind_at=remind_at, created_by=user)
        LeadActivity.objects.create(lead=lead, actor=user, action=LeadActivity.ActionType.NOTE,
                                    notes=f'Task added: {task.title}', meta={'task': str(task.pk)})
    return task


def update(user, task: LeadTask, **changes) -> LeadTask:
    """Fields: title, notes, kind, due_at, remind_at, assignee, status."""
    if not may_edit(user, task):
        raise TaskError('You may not change this task.')
    staff = user.role == 'admin' or _actor(user)[1]
    now = timezone.now()
    with transaction.atomic():
        task = LeadTask.objects.select_for_update().select_related('lead', 'organization').get(pk=task.pk)
        status = changes.pop('status', None) or task.status
        if 'assignee' in changes:
            new = changes.pop('assignee')
            if (new.pk if new else None) != task.assignee_id:
                if not staff:
                    raise TaskError('Only managers reassign tasks.', 'assignee')
                if new is not None and not _agents(task.organization).filter(pk=new.pk).exists():
                    raise TaskError('The assignee must be an active agent of this organization.', 'assignee')
                task.assignee, task.reminded_at = new, None
        if 'due_at' in changes or 'remind_at' in changes:
            if status != T.OPEN:
                raise TaskError('Reopen the task before moving it.', 'due_at')
            due = changes.pop('due_at', None) or task.due_at
            if 'remind_at' in changes:
                remind = changes.pop('remind_at') or due
            else:                                   # keep the same time before the due time
                remind = due - (task.due_at - task.remind_at)
            _check_times(due, remind, now)
            task.due_at, task.remind_at, task.reminded_at = due, remind, None
        for field in ('title', 'notes', 'kind'):
            if field in changes:
                setattr(task, field, changes.pop(field))
        if status != task.status:
            task.status = status
            task.completed_at = now if status == T.DONE else None
            if status == T.OPEN and task.remind_at > now:
                task.reminded_at = None
            LeadActivity.objects.create(lead=task.lead, actor=user, action=LeadActivity.ActionType.NOTE,
                                        notes=f'Task {task.get_status_display().lower()}: {task.title}',
                                        meta={'task': str(task.pk)})
        task.save()
    return task


def label(task: LeadTask) -> str:
    from apps.markets.services import org_timezone
    return f'{task.title}, due {task.due_at.astimezone(org_timezone(task.organization)):%a %d %b, %H:%M}'


def send_reminders(now=None) -> int:
    """Due reminders, each claimed once (a second run or worker sends nothing)."""
    from apps.notifications.services import notify_user
    now, sent = now or timezone.now(), 0
    due = (LeadTask.objects.filter(status=T.OPEN, reminded_at__isnull=True, remind_at__lte=now,
                                   assignee__isnull=False, assignee__user__is_active=True)
           .select_related('assignee__user', 'organization')[:500])
    for task in due:
        try:
            with transaction.atomic():
                if not LeadTask.objects.filter(pk=task.pk, status=T.OPEN, reminded_at__isnull=True,
                                               remind_at=task.remind_at).update(reminded_at=now):
                    continue
                notify_user(task.assignee.user, title='Task due', message=f'Task due: {label(task)}.',
                            event_type='lead_updates', send_email=True, path=f'/leads/{task.lead_id}/tasks',
                            event='lead_task.due')
                sent += 1
        except Exception as exc:
            logger.warning('lead_task.reminder_failed id=%s: %s', task.pk, exc)
    return sent


def erase_user(user) -> int:
    """Tasks about this person keep their dates; what staff wrote about them goes."""
    return LeadTask.objects.filter(lead__user=user).update(title=REDACTED, notes=REDACTED)
