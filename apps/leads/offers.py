"""
Offers register on leads (T-4060, FR-CRM-009, ADR-068).

Who: as for tasks (ADR-057) — staff of the lead's organization see and record
offers on every lead there; an agent only on leads assigned to them; platform
admins everywhere. Membership decides, not ``user.role``.

Lifecycle: ``open`` → ``accepted`` | ``rejected`` | ``withdrawn`` | ``expired``,
or ``countered`` when the other side answers with a new offer (a new row with
``previous``). Only an open offer changes. One accepted offer per lead and
listing. Recording an offer moves the lead to the organization's ``offer``
pipeline stage when the lead is in an earlier open stage; accepting moves
nothing (the deal is not closed). No money moves and no listing changes.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Lead, LeadActivity, LeadOffer

logger = logging.getLogger(__name__)

O = LeadOffer.Status
STAFF = ('owner', 'org_admin', 'team_manager', 'sales_manager', 'crm_operator')
AGENTS = ('agent', 'freelance_agent')
DECISIONS = {O.ACCEPTED, O.REJECTED, O.WITHDRAWN}
MAX_AMOUNT = 10 ** 13
HORIZON = timedelta(days=366)
REDACTED = '[REDACTED]'


class OfferError(Exception):
    def __init__(self, message: str, field: str = 'detail', code: str = ''):
        super().__init__(message)
        self.field, self.code = field, code


def _actor(user):
    from apps.core.permissions import active_org_role, get_user_org
    org, role = get_user_org(user), active_org_role(user)
    agent = getattr(user, 'agent_profile', None) if role in AGENTS else None
    return org, role in STAFF, agent


def visible(user):
    qs = LeadOffer.objects.select_related('lead__user', 'property', 'created_by', 'organization')
    if user.role == 'admin':
        return qs
    org, staff, agent = _actor(user)
    if org is None:
        return qs.none()
    qs = qs.filter(organization=org)
    if staff:
        return qs
    if agent is not None:
        return qs.filter(lead__assigned_agent=agent)
    return qs.none()


def may_handle(user, lead: Lead) -> bool:
    if user.role == 'admin':
        return True
    org, staff, agent = _actor(user)
    if org is None or org.pk != lead.organization_id:
        return False
    return staff or (agent is not None and lead.assigned_agent_id == agent.pk)


def expire_due(now=None, qs=None) -> int:
    """Open offers past their expiry become ``expired`` (on read, and from the beat job)."""
    now = now or timezone.now()
    rows = (qs if qs is not None else LeadOffer.objects).filter(status=O.OPEN, expires_at__lte=now)
    return LeadOffer.objects.filter(pk__in=list(rows.values_list('pk', flat=True)[:1000]), status=O.OPEN) \
        .update(status=O.EXPIRED, decided_at=now, updated_at=now)


def _listing(lead: Lead, property_id):
    if property_id is None:
        return None
    from apps.properties.models import Property
    p = Property.objects.filter(pk=property_id).first()
    if p is None or p.organization_id != lead.organization_id:
        raise OfferError("Not a listing of this lead's organization.", 'property')
    return p


def _check(amount, currency, expires_at, now):
    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 < amount <= MAX_AMOUNT:
        raise OfferError(f'A whole amount from 1 to {MAX_AMOUNT:,}.', 'amount')
    if not (isinstance(currency, str) and len(currency) == 3 and currency.isascii() and currency.isalpha()):
        raise OfferError('An ISO currency code, e.g. PKR.', 'currency')
    if expires_at is not None and not now < expires_at <= now + HORIZON:
        raise OfferError('Between now and a year ahead.', 'expires_at')


def _activity(lead, user, offer, verb):
    LeadActivity.objects.create(
        lead=lead, actor=user, action=LeadActivity.ActionType.OFFER,
        notes=f'Offer {verb}: {offer.currency} {offer.amount:,} ({offer.get_party_display()})'[:500],
        meta={'offer': str(offer.pk), 'status': offer.status, 'amount': offer.amount, 'currency': offer.currency,
              **({'property': str(offer.property_id)} if offer.property_id else {})})


def _audit(action, offer, user, meta=None):
    from apps.core.audit import safe_record
    safe_record(action, category='organization', actor=user, organization=offer.organization, target=offer,
                meta={'lead': str(offer.lead_id), 'status': offer.status, **(meta or {})})


def _advance(lead: Lead, user):
    """Into the organization's ``offer`` stage when the lead is in an earlier open stage."""
    from . import pipeline as PL
    from .models import PipelineStage
    K = PipelineStage.Kind
    target = next((s for s in PL.stages(lead.organization) if s.key == 'offer' and s.kind == K.OPEN), None)
    if target is None:
        return
    lead = Lead.objects.select_for_update(of=('self',)).select_related('stage', 'organization').get(pk=lead.pk)
    now_stage = PL.current(lead)
    if now_stage is None or (now_stage.kind == K.OPEN and now_stage.position < target.position):
        PL._move(lead, target, user, current_stage=now_stage, note='Offer recorded')


def create(user, lead: Lead, *, amount, currency, party=LeadOffer.Party.CLIENT, basis=LeadOffer.Basis.TOTAL,
           property_id=None, conditions: str = '', expires_at=None, previous: LeadOffer | None = None) -> LeadOffer:
    if lead.organization_id is None:
        raise OfferError('Offers need a lead of an organization.', 'lead')
    if not may_handle(user, lead):
        raise OfferError('Lead not found.', 'lead')
    now = timezone.now()
    currency = (currency or '').upper()
    _check(amount, currency, expires_at, now)
    if previous is not None:
        expire_due(now, LeadOffer.objects.filter(pk=previous.pk))
    with transaction.atomic():
        if previous is not None:
            previous = LeadOffer.objects.select_for_update().get(pk=previous.pk)
            if previous.lead_id != lead.pk:
                raise OfferError('That offer is on another lead.', 'previous')
            if previous.status != O.OPEN:
                raise OfferError(f'That offer is {previous.status}; only an open offer can be countered.',
                                 'previous', 'offer.not_open')
            if party == previous.party:
                raise OfferError('A counter-offer comes from the other side.', 'party')
            if property_id is None:
                property_id = previous.property_id
            elif str(property_id) != str(previous.property_id):
                raise OfferError('A counter-offer is for the same listing.', 'property')
            if currency != previous.currency or basis != previous.basis:
                raise OfferError('A counter-offer keeps the currency and basis.', 'currency')
        listing = _listing(lead, property_id)
        offer = LeadOffer.objects.create(
            lead=lead, organization_id=lead.organization_id, property=listing, previous=previous, party=party,
            amount=amount, currency=currency, basis=basis, conditions=(conditions or '').strip()[:1000],
            expires_at=expires_at, created_by=user)
        if previous is not None:
            LeadOffer.objects.filter(pk=previous.pk).update(status=O.COUNTERED, decided_at=now, updated_at=now)
        _activity(lead, user, offer, 'countered' if previous else 'recorded')
        _advance(lead, user)
    _audit('lead.offer_recorded', offer, user, {'counter_to': str(previous.pk)} if previous else None)
    return offer


def decide(user, offer: LeadOffer, status: str, note: str = '') -> LeadOffer:
    """Accept, reject or withdraw an open offer."""
    if status not in DECISIONS:
        raise OfferError('accepted, rejected or withdrawn.', 'status')
    if not may_handle(user, offer.lead):
        raise OfferError('You may not change this offer.')
    now = timezone.now()
    expire_due(now, LeadOffer.objects.filter(pk=offer.pk))      # outside: a refusal must not undo it
    try:
        with transaction.atomic():
            offer = LeadOffer.objects.select_for_update().select_related('lead', 'organization').get(pk=offer.pk)
            if offer.status != O.OPEN:
                raise OfferError(f'This offer is {offer.status}.', 'status', 'offer.not_open')
            offer.status, offer.decided_at, offer.decision_note = status, now, (note or '').strip()[:300]
            offer.save(update_fields=['status', 'decided_at', 'decision_note', 'updated_at'])
            _activity(offer.lead, user, offer, status)
    except IntegrityError:
        raise OfferError('Another offer for this listing is already accepted on this lead.', 'status',
                         'offer.already_accepted') from None
    _audit(f'lead.offer_{status}', offer, user)
    return offer


def chain(offer: LeadOffer) -> list[LeadOffer]:
    """The negotiation this offer belongs to, oldest first."""
    first = offer
    seen = {first.pk}
    while first.previous_id and first.previous_id not in seen:
        first = LeadOffer.objects.get(pk=first.previous_id)
        seen.add(first.pk)
    out, cur = [first], first
    while True:
        nxt = LeadOffer.objects.filter(previous=cur).first()
        if nxt is None or nxt.pk in {o.pk for o in out}:
            return out
        out.append(nxt)
        cur = nxt


def summary(qs) -> dict:
    """Counts by status (for a lead or the organization)."""
    from django.db.models import Count
    rows = dict(qs.values_list('status').annotate(n=Count('pk')).values_list('status', 'n'))
    return {s: rows.get(s, 0) for s in O.values}


def erase_user(user) -> int:
    """Offers about this person keep amounts and dates; what staff wrote goes."""
    return LeadOffer.objects.filter(lead__user=user).update(conditions=REDACTED, decision_note='')

