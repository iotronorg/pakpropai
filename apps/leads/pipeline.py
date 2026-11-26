"""
Configurable pipeline stages (T-3030, FR-CRM-002, ADR-062).

Each organization has an ordered list of stages; a lead sits in one of them.
``Lead.status`` keeps meaning the AI's qualification (new, warm, qualified,
cold, blocked) and ``routing_state`` keeps meaning who holds the lead; moving a
lead to a won or lost stage closes its routing, and moving it back reopens it.

A lead without a stage (every lead from before stages, and every new lead) is
in the organization's first open stage, so nothing has to be backfilled. A
stage from another organization (the lead was routed elsewhere) counts the same.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify

from .models import Lead, LeadActivity, PipelineStage

K = PipelineStage.Kind
DEFAULTS = (('new', 'New', K.OPEN), ('contacted', 'Contacted', K.OPEN), ('qualified', 'Qualified', K.OPEN),
            ('viewing', 'Viewing', K.OPEN), ('offer', 'Offer', K.OPEN), ('won', 'Won', K.WON),
            ('lost', 'Lost', K.LOST))
MAX_STAGES = 20
STAFF = ('owner', 'org_admin', 'team_manager', 'sales_manager', 'crm_operator')
ADMINS = ('owner', 'org_admin')
AGENTS = ('agent', 'freelance_agent')


class PipelineError(Exception):
    def __init__(self, message: str, field: str = 'detail', code: str = 'pipeline.invalid'):
        super().__init__(message)
        self.field, self.code = field, code


# ── Reading ───────────────────────────────────────────────────────────────────

def stages(org, *, archived: bool = False) -> list[PipelineStage]:
    """The organization's stages in order; the defaults are created on first use."""
    qs = PipelineStage.objects.filter(organization=org)
    if not qs.exists():
        PipelineStage.objects.bulk_create(
            [PipelineStage(organization=org, key=k, name=n, kind=kind, position=i)
             for i, (k, n, kind) in enumerate(DEFAULTS)], ignore_conflicts=True)
    if not archived:
        qs = qs.filter(archived_at__isnull=True)
    return list(qs.order_by('position', 'created_at'))


def first_stage(org) -> PipelineStage | None:
    return next((s for s in stages(org) if s.kind == K.OPEN), None)


def current(lead: Lead, first: PipelineStage | None = None) -> PipelineStage | None:
    if lead.organization_id is None:
        return None
    stage = lead.stage if lead.stage_id else None
    if stage is not None and stage.organization_id == lead.organization_id and stage.archived_at is None:
        return stage
    return first if first is not None else first_stage(lead.organization)


def in_stage(qs, org, stage: PipelineStage):
    """Leads of ``org`` in ``stage``; the first stage also holds the leads without one."""
    qs = qs.filter(organization=org)
    first = first_stage(org)
    if first is not None and stage.pk == first.pk:
        return qs.filter(Q(stage=stage) | Q(stage__isnull=True) | ~Q(stage__organization=org)
                         | Q(stage__archived_at__isnull=False))
    return qs.filter(stage=stage)


def counts(qs, org) -> dict:
    """Lead count per stage id, the unstaged ones added to the first stage."""
    active = stages(org)
    qs = qs.filter(organization=org).order_by()
    raw = dict(qs.filter(stage__organization=org, stage__archived_at__isnull=True)
               .values_list('stage').annotate(n=Count('pk')))
    first = first_stage(org)
    out = {s.pk: raw.get(s.pk, 0) for s in active}
    if first is not None:
        out[first.pk] += qs.count() - sum(raw.values())
    return out


def stage_dict(stage: PipelineStage | None) -> dict | None:
    if stage is None:
        return None
    return {'id': str(stage.pk), 'key': stage.key, 'name': stage.name, 'kind': stage.kind}


# ── Who may do what ───────────────────────────────────────────────────────────

def actor(user):
    """(organization, membership role) of the caller's active membership."""
    from apps.core.permissions import active_org_role, get_user_org
    return get_user_org(user), active_org_role(user)


def may_move(user, lead: Lead) -> bool:
    if user.role == 'admin':
        return True
    org, role = actor(user)
    if org is None or org.pk != lead.organization_id:
        return False
    if role in STAFF:
        return True
    agent = getattr(user, 'agent_profile', None) if role in AGENTS else None
    return agent is not None and agent.pk == lead.assigned_agent_id


# ── Changing the stages ───────────────────────────────────────────────────────

def _name(name) -> str:
    name = ' '.join(str(name or '').split())
    if not name or len(name) > 60:
        raise PipelineError('A name of 1 to 60 characters.', 'name')
    return name


def _kind(kind) -> str:
    if kind not in K.values:
        raise PipelineError(f'One of {", ".join(K.values)}.', 'kind')
    return kind


def _locked(org):
    """The organization's active stages, locked for the change."""
    stages(org)
    return list(PipelineStage.objects.select_for_update().filter(organization=org, archived_at__isnull=True)
                .order_by('position', 'created_at'))


def _renumber(ordered):
    for i, s in enumerate(ordered):
        if s.position != i:
            s.position = i
            s.save(update_fields=['position'])


@transaction.atomic
def add(org, *, name, kind=K.OPEN, after: str | None = None, user=None) -> PipelineStage:
    name, kind = _name(name), _kind(kind)
    active = _locked(org)
    if len(active) >= MAX_STAGES:
        raise PipelineError(f'At most {MAX_STAGES} stages.', 'detail', 'pipeline.too_many')
    if any(s.name.lower() == name.lower() for s in active):
        raise PipelineError('A stage with this name exists.', 'name', 'pipeline.duplicate')
    base = slugify(name)[:32] or 'stage'
    taken = set(PipelineStage.objects.filter(organization=org).values_list('key', flat=True))
    key, n = base, 2
    while key in taken:
        key, n = f'{base}-{n}', n + 1
    index = len(active)
    if after:
        pos = next((i for i, s in enumerate(active) if str(s.pk) == str(after)), None)
        if pos is None:
            raise PipelineError('Not one of this pipeline\'s stages.', 'after')
        index = pos + 1
    stage = PipelineStage.objects.create(organization=org, key=key, name=name, kind=kind, position=index)
    active.insert(index, stage)
    _renumber(active)
    _audit('pipeline.stage_added', org, stage, user, {'key': key, 'kind': kind})
    return stage


@transaction.atomic
def update(stage: PipelineStage, *, name=None, kind=None, user=None) -> PipelineStage:
    active = _locked(stage.organization)
    stage = next((s for s in active if s.pk == stage.pk), None)
    if stage is None:
        raise PipelineError('This stage is archived.', 'detail', 'pipeline.archived')
    fields = []
    if name is not None:
        name = _name(name)
        if any(s.pk != stage.pk and s.name.lower() == name.lower() for s in active):
            raise PipelineError('A stage with this name exists.', 'name', 'pipeline.duplicate')
        stage.name = name
        fields.append('name')
    if kind is not None and _kind(kind) != stage.kind:
        if stage.kind == K.OPEN and not any(s.kind == K.OPEN and s.pk != stage.pk for s in active):
            raise PipelineError('The pipeline needs at least one open stage.', 'kind', 'pipeline.last_open')
        if in_stage(Lead.objects.all(), stage.organization, stage).exists():
            raise PipelineError('Move the leads out before changing what this stage means.', 'kind',
                                'pipeline.not_empty')
        stage.kind = kind
        fields.append('kind')
    if fields:
        stage.save(update_fields=fields)
        _audit('pipeline.stage_changed', stage.organization, stage, user, {f: getattr(stage, f) for f in fields})
    return stage


@transaction.atomic
def reorder(org, ids: list, *, user=None) -> list[PipelineStage]:
    active = _locked(org)
    by_id = {str(s.pk): s for s in active}
    ids = [str(i) for i in ids]
    if sorted(ids) != sorted(by_id):
        raise PipelineError('List every active stage exactly once.', 'order')
    ordered = [by_id[i] for i in ids]
    first_before = next((s for s in active if s.kind == K.OPEN), None)
    first_after = next((s for s in ordered if s.kind == K.OPEN), None)
    if first_before is not None and first_after is not None and first_before.pk != first_after.pk:
        # The leads without a stage belong to the first open stage: keep them where they were.
        Lead.objects.filter(organization=org).filter(
            Q(stage__isnull=True) | ~Q(stage__organization=org)).update(stage=first_before)
    _renumber(ordered)
    _audit('pipeline.stages_reordered', org, None, user, {'order': [s.key for s in ordered]})
    return ordered


@transaction.atomic
def archive(stage: PipelineStage, *, move_to: str | None, user=None) -> int:
    """Archive a stage; its leads go to ``move_to``. Returns how many leads moved."""
    org = stage.organization
    active = _locked(org)
    if stage.pk not in {s.pk for s in active}:
        raise PipelineError('This stage is archived.', 'detail', 'pipeline.archived')
    rest = [s for s in active if s.pk != stage.pk]
    if not any(s.kind == K.OPEN for s in rest):
        raise PipelineError('The pipeline needs at least one open stage.', 'detail', 'pipeline.last_open')
    target = next((s for s in rest if str(s.pk) == str(move_to)), None) if move_to else None
    leads = in_stage(Lead.objects.all(), org, stage)
    moved = 0
    if leads.exists():
        if target is None:
            raise PipelineError('Choose an active stage for its leads.', 'move_to', 'pipeline.move_to')
        moved = leads.count()
        for lead in leads.select_related('stage'):
            _move(lead, target, user, current_stage=stage, note='stage archived')
    stage.archived_at = timezone.now()
    stage.save(update_fields=['archived_at'])
    _renumber(rest)
    _audit('pipeline.stage_archived', org, stage, user, {'key': stage.key, 'moved': moved,
                                                         'to': target.key if target else None})
    return moved


# ── Moving a lead ─────────────────────────────────────────────────────────────

def move(lead: Lead, stage_id, *, user=None, note: str = '') -> Lead:
    if lead.organization_id is None:
        raise PipelineError('This lead is not with an organization yet.', 'detail', 'pipeline.no_organization')
    with transaction.atomic():
        lead = Lead.objects.select_for_update(of=('self',)).select_related('stage', 'organization').get(pk=lead.pk)
        target = next((s for s in stages(lead.organization) if str(s.pk) == str(stage_id)), None)
        if target is None:
            raise PipelineError('Not an active stage of this lead\'s organization.', 'stage')
        before = current(lead)
        if before is not None and before.pk == target.pk and lead.stage_id == target.pk:
            return lead
        _move(lead, target, user, current_stage=before, note=note)
    return lead


def _move(lead: Lead, target: PipelineStage, user, *, current_stage, note=''):
    R = Lead.RoutingState
    fields = ['stage', 'stage_changed_at']
    lead.stage, lead.stage_changed_at = target, timezone.now()
    routing_before = lead.routing_state
    if target.kind in (K.WON, K.LOST):
        lead.routing_state = R.CLOSED
    elif lead.routing_state == R.CLOSED:
        lead.routing_state = R.AGENT_ASSIGNED if lead.assigned_agent_id else R.ORG_QUEUE
    if lead.routing_state != routing_before:
        fields.append('routing_state')
    lead.save(update_fields=fields)
    LeadActivity.objects.create(
        lead=lead, actor=user if getattr(user, 'is_authenticated', False) else None,
        action=LeadActivity.ActionType.STAGE, notes=str(note or '')[:500],
        meta={'from': current_stage.key if current_stage else None, 'to': target.key, 'kind': target.kind,
              **({'routing': [routing_before, lead.routing_state]} if 'routing_state' in fields else {})})


def _audit(action, org, stage, user, meta):
    from apps.core.audit import safe_record
    safe_record(action, category='organization', actor=user, organization=org, target=stage,
                target_type='PipelineStage', meta=meta)


def board(qs, org, *, per_stage: int = 20) -> list[dict]:
    """Stages in order with their count and first leads (highest priority, then score)."""
    out = []
    totals = counts(qs, org)
    for stage in stages(org):
        leads = (in_stage(qs, org, stage).select_related('user', 'assigned_agent')
                 .order_by('-priority', '-score', '-created_at')[:per_stage])
        out.append({**stage_dict(stage), 'position': stage.position, 'count': totals.get(stage.pk, 0),
                    'leads': [card(lead) for lead in leads]})
    return out


def card(lead: Lead) -> dict:
    """What a board card shows; no phone (the lead's own page applies the sharing rules)."""
    return {'id': str(lead.pk), 'name': lead.user.name if lead.user_id else None, 'intent': lead.intent,
            'status': lead.status, 'score': lead.score, 'priority': lead.priority,
            'routing_state': lead.routing_state, 'location_interest': lead.city_interest,
            'budget_min': lead.budget_min, 'budget_max': lead.budget_max, 'budget_currency': lead.budget_currency,
            'assigned_agent_id': str(lead.assigned_agent_id) if lead.assigned_agent_id else None,
            'assigned_agent_name': lead.assigned_agent.name if lead.assigned_agent_id else None,
            'last_contacted_at': lead.last_contacted_at, 'stage_changed_at': lead.stage_changed_at,
            'created_at': lead.created_at}
