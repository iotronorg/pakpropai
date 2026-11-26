"""Viewing slots, booking and feedback (T-2060, ADR-037)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.agents.models import Agent
from apps.enquiries import services as E
from apps.enquiries.models import Enquiry
from apps.leads.models import Appointment, Lead, LeadActivity
from apps.markets.services import market_timezone
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property

from .models import AvailabilityException, AvailabilityRule, ViewingFeedback

logger = logging.getLogger(__name__)

A = Appointment.Status
ACTIVE = (A.SCHEDULED, A.CONFIRMED, A.RESCHEDULED)
NOTICE = timedelta(hours=2)
HORIZON = timedelta(days=60)
MAX_WINDOW_DAYS = 14
MAX_UPCOMING = 5
MAX_DURATION = 480
UTC = dt_tz.utc
CHANGE_TEXT = 'To change or cancel it, reply here or use RealTron.'


class ViewingError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Slot:
    listing_id: str
    agent_id: int
    start: datetime                       # UTC
    minutes: int

    @property
    def end(self):
        return self.start + timedelta(minutes=self.minutes)

    @property
    def id(self) -> str:
        return f'{self.listing_id}@{self.start:%Y%m%dT%H%MZ}'


def parse_slot_id(value: str) -> tuple[uuid.UUID, datetime]:
    try:
        listing, _, stamp = (value or '').partition('@')
        return uuid.UUID(listing), datetime.strptime(stamp, '%Y%m%dT%H%MZ').replace(tzinfo=UTC)
    except ValueError:
        raise ViewingError('Unknown slot.', 'slot_unknown') from None


def listing_timezone(p: Property | None) -> ZoneInfo:
    return market_timezone((p.market_id or p.country) if p else None)


def local_text(start: datetime, tz: ZoneInfo) -> str:
    return f'{start.astimezone(tz):%a %d %b %Y, %H:%M} ({tz.key})'


# ── Availability ──────────────────────────────────────────────────────────────

def _to_utc(day: date, at, tz: ZoneInfo) -> datetime | None:
    """None for a local time that does not exist (clock change)."""
    local = datetime.combine(day, at, tzinfo=tz)
    utc = local.astimezone(UTC)
    return utc if utc.astimezone(tz).replace(tzinfo=None) == local.replace(tzinfo=None) else None


def _open_slots(agent_ids, start: datetime, end: datetime):
    """``(agent_id, start, minutes)`` from rules and exceptions, before bookings."""
    rules, exceptions = {}, {}
    for r in AvailabilityRule.objects.filter(agent_id__in=agent_ids):
        rules.setdefault(r.agent_id, []).append(r)
    first, last = start.date() - timedelta(days=1), end.date() + timedelta(days=1)
    for x in AvailabilityException.objects.filter(agent_id__in=agent_ids, date__gte=first, date__lte=last):
        exceptions.setdefault(x.agent_id, []).append(x)

    for agent_id in agent_ids:
        mine, extra = rules.get(agent_id, []), exceptions.get(agent_id, [])
        closed_days, closed, spans = {x.date for x in extra if not x.available and x.start is None}, [], []
        for x in extra:
            if x.start is None:
                continue
            tz = ZoneInfo(x.timezone)
            a, b = _to_utc(x.date, x.start, tz), _to_utc(x.date, x.end, tz)
            if a is None or b is None:
                continue
            if x.available:
                spans.append((a, b, mine[0].slot_minutes if mine else 60))
            else:
                closed.append((a, b))
        day = first
        while day <= last:
            if day not in closed_days:
                for r in mine:
                    if r.weekday == day.weekday():
                        tz = ZoneInfo(r.timezone)
                        a, b = _to_utc(day, r.start, tz), _to_utc(day, r.end, tz)
                        if a and b:
                            spans.append((a, b, r.slot_minutes))
            day += timedelta(days=1)
        for a, b, minutes in spans:
            step = timedelta(minutes=minutes)
            while a + step <= b:
                if start <= a < end and not any(a < y and a + step > x for x, y in closed):
                    yield agent_id, a, minutes
                a += step


def busy(agent_ids, start: datetime, end: datetime, exclude=None) -> list[tuple]:
    """Active appointments of these agents that touch the window."""
    qs = Appointment.objects.filter(agent_id__in=agent_ids, status__in=ACTIVE, scheduled_at__lt=end,
                                    scheduled_at__gt=start - timedelta(days=1))
    if exclude:
        qs = qs.exclude(pk=exclude)
    rows = ((a, s, s + timedelta(minutes=m))
            for a, s, m in qs.values_list('agent_id', 'scheduled_at', 'duration_minutes'))
    return [r for r in rows if r[2] > start]


def candidates(p: Property, user=None) -> list[int]:
    """
    Agents who may show this listing: the visitor's assigned agent at this
    organization, else the listing's agent, else any agent of the organization.
    """
    qs = Agent.objects.filter(is_active=True, user__is_active=True, availability_rules__isnull=False).distinct()
    T = Property.ListingOwnerType
    if p.listing_owner_type == T.FREELANCE_AGENT:
        return list(qs.filter(pk=p.listed_by_agent_id).values_list('pk', flat=True))
    if p.listing_owner_type != T.ORGANIZATION or not p.organization.is_active:
        return []                                       # private owners arrange it in the thread
    members = qs.filter(user_id__in=OrganizationMembership.objects.filter(
        organization=p.organization_id, is_active=True).exclude(
        role=OrganizationMembership.Role.VIEWER).values('user_id'))
    lead = (Lead.objects.filter(user=user, organization=p.organization_id, assigned_agent__isnull=False)
            .order_by('-created_at').first() if user else None)
    if lead:
        return list(members.filter(pk=lead.assigned_agent_id).values_list('pk', flat=True))
    own = list(members.filter(pk=p.listed_by_agent_id).values_list('pk', flat=True))
    return own or list(members.order_by('pk').values_list('pk', flat=True))


def slots(p: Property, start: datetime, end: datetime, user=None, exclude=None) -> list[Slot]:
    """Free slots, one per start time, soonest first."""
    now = timezone.now()
    start, end = max(start, now + NOTICE), min(end, now + HORIZON)
    agents = candidates(p, user) if start < end else []
    if not agents:
        return []
    taken = {}
    for agent_id, s, e in busy(agents, start, end + timedelta(minutes=MAX_DURATION), exclude):
        taken.setdefault(agent_id, []).append((s, e))
    out = {}
    for agent_id, at, minutes in _open_slots(agents, start, end):
        slot = Slot(str(p.pk), agent_id, at, minutes)
        if at not in out and not any(at < e and slot.end > s for s, e in taken.get(agent_id, ())):
            out[at] = slot
    return [out[k] for k in sorted(out)]


def slot_at(p: Property, start: datetime, user=None, exclude=None) -> Slot | None:
    return next((s for s in slots(p, start, start + timedelta(minutes=1), user, exclude) if s.start == start), None)


# ── Booking ───────────────────────────────────────────────────────────────────

def lock_agent(agent_id) -> None:
    """Every change of an agent's calendar takes this lock first."""
    Agent.objects.select_for_update().get(pk=agent_id)


def check_free(agent_id, start: datetime, minutes: int, exclude=None) -> None:
    if busy([agent_id], start, start + timedelta(minutes=minutes), exclude):
        raise ViewingError('The agent already has an appointment at this time.', 'slot_taken')


def _hold(p, start, user, exclude=None) -> Slot:
    """Lock the agent, then look again: the slot may have gone meanwhile."""
    slot = slot_at(p, start, user, exclude)
    if slot is None:
        raise ViewingError('This time is not available any more.', 'slot_taken')
    lock_agent(slot.agent_id)
    again = slot_at(p, start, user, exclude)
    if again is None or again.agent_id != slot.agent_id:
        raise ViewingError('This time is not available any more.', 'slot_taken')
    return again


def _check_visitor(user, slot: Slot, exclude=None) -> None:
    mine = Appointment.objects.filter(lead__user=user, status__in=ACTIVE, scheduled_at__gt=timezone.now())
    if exclude:
        mine = mine.exclude(pk=exclude)
    if mine.count() >= MAX_UPCOMING:
        raise ViewingError(f'You already have {MAX_UPCOMING} upcoming viewings.', 'limit')
    if any(a.scheduled_at < slot.end and a.ends_at > slot.start for a in mine):
        raise ViewingError('You already have a viewing at this time.', 'visitor_busy')
    if mine.filter(property_id=slot.listing_id).exists():
        raise ViewingError('You already have a viewing of this listing. Reschedule it instead.', 'already_booked')


def book(user, p: Property, start: datetime, *, note: str = '', participants=(), via: str = 'api',
         source: str = Lead.Source.WEB, preferred_channel: str = Enquiry.Channel.IN_APP) -> Appointment:
    """Book a free slot. The caller has the user's consent to share contact details."""
    from django.contrib.auth import get_user_model
    tz = listing_timezone(p)
    with transaction.atomic():
        slot = _hold(p, start, user)
        get_user_model().objects.select_for_update().get(pk=user.pk)
        _check_visitor(user, slot)
        text = f'Viewing request: {local_text(start, tz)}' + (f'\n{note}' if note else '')
        e, _ = E.create(user, message=text, listing=p, via=via, source=source, preferred_channel=preferred_channel)
        agent = Agent.objects.select_related('user').get(pk=slot.agent_id)
        lead = e.lead
        if lead.assigned_agent_id is None:
            lead.assigned_agent, lead.routing_state = agent, Lead.RoutingState.AGENT_ASSIGNED
            lead.save(update_fields=['assigned_agent', 'routing_state'])
            LeadActivity.objects.create(lead=lead, action=LeadActivity.ActionType.ASSIGNED,
                                        notes='Assigned by viewing booking', meta={'new_agent_id': agent.pk})
        appt = Appointment.objects.create(
            lead=lead, enquiry=e, property=p, agent=agent, scheduled_at=start, duration_minutes=slot.minutes,
            timezone=tz.key, participants=list(participants), created_by=user)
        LeadActivity.objects.create(lead=lead, actor=user, action=LeadActivity.ActionType.NOTE,
                                    notes='Viewing booked', meta={'appointment': str(appt.pk)})
        notify_agent(appt, 'New viewing booked')
    logger.info('viewing.booked id=%s via=%s', appt.pk, via)
    return appt


def reschedule(appt: Appointment, user, start: datetime) -> Appointment:
    """The visitor moves their viewing to another free slot of the same listing."""
    with transaction.atomic():
        p = E.visible_listing(appt.property_id) if appt.property_id else None
        if p is None:
            raise ViewingError('This listing is no longer available.', 'listing_unavailable')
        if appt.enquiry_id:
            from apps.enquiries.safety import blocked_sides
            e = appt.enquiry
            if blocked_sides(e.user_id, e.kind, e.organization_id, e.responder_id):
                raise ViewingError('This listing is no longer available.', 'listing_unavailable')
        slot = _hold(p, start, user, exclude=appt.pk)
        appt = Appointment.objects.select_for_update().get(pk=appt.pk)
        _check_open(appt)
        _check_visitor(user, slot, exclude=appt.pk)
        appt.agent_id, appt.scheduled_at, appt.duration_minutes = slot.agent_id, start, slot.minutes
        appt.status = A.RESCHEDULED
        reset_reminders(appt)
        appt.save()
        notify_agent(appt, 'Viewing rescheduled')
    return appt


def reset_reminders(appt: Appointment) -> None:
    appt.reminder_sent_at = appt.day_reminder_sent_at = appt.feedback_requested_at = None


def _check_open(appt: Appointment) -> None:
    if appt.status not in ACTIVE or appt.scheduled_at <= timezone.now():
        raise ViewingError('This viewing can no longer be changed.', 'closed')


def cancel(appt: Appointment) -> Appointment:
    with transaction.atomic():
        appt = Appointment.objects.select_for_update().get(pk=appt.pk)
        _check_open(appt)
        appt.status = A.CANCELLED
        appt.save(update_fields=['status', 'updated_at'])
        notify_agent(appt, 'Viewing cancelled')
    return appt


def notify_agent(appt: Appointment, title: str) -> None:
    from apps.notifications.services import notify_user
    if appt.agent and appt.agent.user:
        what = appt.property.title if appt.property else 'Viewing'
        notify_user(appt.agent.user, title=title,
                    message=f'{title}: {what}, {local_text(appt.scheduled_at, zone(appt))}.',
                    event_type='appointment_reminders', send_email=True, path=f'/appointments/{appt.pk}')


def zone(appt: Appointment) -> ZoneInfo:
    from apps.markets.services import org_timezone
    if appt.timezone:
        return ZoneInfo(appt.timezone)
    return org_timezone(appt.lead.organization)


# ── Feedback ──────────────────────────────────────────────────────────────────

def give_feedback(appt: Appointment, author, side: str, *, rating: int, interested=None, notes: str = ''):
    if appt.status not in (*ACTIVE, A.COMPLETED) or appt.ends_at > timezone.now():
        raise ViewingError('Feedback is possible after the viewing.', 'not_finished')
    with transaction.atomic():
        fb, created = ViewingFeedback.objects.update_or_create(
            appointment=appt, side=side,
            defaults={'author': author, 'rating': rating, 'interested': interested, 'notes': notes})
        if created:
            LeadActivity.objects.create(lead=appt.lead, actor=author, action=LeadActivity.ActionType.NOTE,
                                        notes=f'Viewing feedback ({side}): {rating}/5',
                                        meta={'appointment': str(appt.pk), 'interested': interested})
    return fb


# ── Reminders ─────────────────────────────────────────────────────────────────

def _claim(appt, field) -> bool:
    """Once per appointment and time, also when the task runs twice or the visit moved."""
    return bool(Appointment.objects.filter(
        pk=appt.pk, status=appt.status, scheduled_at=appt.scheduled_at, **{f'{field}__isnull': True},
    ).update(**{field: timezone.now()}))


def _remind(appt, label: str) -> None:
    from apps.notifications.delivery import enqueue_whatsapp_text
    from apps.notifications.models import UserNotificationPreference
    from apps.notifications.services import notify_user
    visitor, tz = appt.lead.user, zone(appt)
    at = appt.scheduled_at.astimezone(tz)
    what = appt.property.title if appt.property else 'the property'
    prefs = UserNotificationPreference.objects.filter(user=visitor).first()
    if prefs is None or (prefs.whatsapp_enabled and prefs.appointment_reminders):
        lines = ['🏠 *Visit Reminder*', '', 'You have a property visit scheduled:', '', f'📍 *{what}*',
                 f'📅 {at:%A, %d %B %Y}', f'🕐 {at:%I:%M %p} ({tz.key})',
                 f'👤 Agent: {appt.agent.display_name if appt.agent else "your agent"}']
        if appt.location:
            lines.append(f'🗺️ {appt.location}')
        if appt.meeting_url:
            lines.append(f'🔗 {appt.meeting_url}')
        enqueue_whatsapp_text(visitor.phone, '\n'.join(lines + ['', change_text()]),
                              key=f'appt_reminder:{appt.pk}:{appt.scheduled_at.isoformat()}:{label}',
                              organization=appt.lead.organization, expires_at=appt.scheduled_at)
    message = f'Visit reminder: {what}, {local_text(appt.scheduled_at, tz)}.'
    notify_user(visitor, title='Visit reminder', message=message, send_whatsapp=False, send_email=True,
                event_type='appointment_reminders', path=f'/viewings/{appt.pk}')
    notify_agent(appt, 'Visit reminder')


def change_text() -> str:
    """A reply reaches the assistant's viewing tools only while they are on (KI-076)."""
    from apps.config.services import SystemConfigService
    if SystemConfigService.get_features().get('feature_talk_to_agent', True):
        return CHANGE_TEXT
    return 'You can change or cancel it in RealTron.'


def send_reminders() -> int:
    """A day and an hour before; by each person's channel preferences."""
    now, sent = timezone.now(), 0
    for field, label, after, before in (('reminder_sent_at', '1h', timedelta(0), timedelta(hours=1)),
                                        ('day_reminder_sent_at', '24h', timedelta(hours=20), timedelta(hours=24))):
        due = Appointment.objects.filter(
            scheduled_at__gt=now + after, scheduled_at__lte=now + before, status__in=ACTIVE,
            **{f'{field}__isnull': True}).select_related('lead__user', 'lead__organization', 'property', 'agent__user')
        for appt in due:
            try:
                with transaction.atomic():
                    if appt.lead.user.is_active and _claim(appt, field):
                        _remind(appt, label)
                        sent += 1
            except Exception as exc:
                logger.warning('viewing.reminder_failed id=%s: %s', appt.pk, exc)
    return sent


def request_feedback() -> int:
    """Ask the visitor once, after the visit."""
    from apps.notifications.services import notify_user
    now, sent = timezone.now(), 0
    for appt in Appointment.objects.filter(
            scheduled_at__lt=now, scheduled_at__gt=now - timedelta(days=2), feedback_requested_at__isnull=True,
            status__in=(A.CONFIRMED, A.COMPLETED)).exclude(feedback__side=ViewingFeedback.Side.USER).select_related(
            'lead__user', 'property'):
        if appt.ends_at + timedelta(hours=1) > now or not appt.lead.user.is_active:
            continue
        with transaction.atomic():
            if _claim(appt, 'feedback_requested_at'):
                what = appt.property.title if appt.property else 'the property'
                notify_user(appt.lead.user, title='How was your visit?', message=f'How was your visit to {what}? '
                            'Your feedback improves your recommendations.', event_type='appointment_reminders',
                            send_email=True, path=f'/viewings/{appt.pk}/feedback')
                sent += 1
    return sent


# ── Privacy ───────────────────────────────────────────────────────────────────

def erase_user(user) -> int:
    """Cancel upcoming visits; remove notes, names and feedback of this person."""
    mine = Appointment.objects.filter(lead__user=user)
    for appt in mine.filter(status__in=ACTIVE, scheduled_at__gt=timezone.now()):
        cancel(appt)
    ViewingFeedback.objects.filter(appointment__in=mine).delete()
    ViewingFeedback.objects.filter(author=user).update(notes='[REDACTED]', author=None)
    return mine.update(notes='[REDACTED]', participants=[])
