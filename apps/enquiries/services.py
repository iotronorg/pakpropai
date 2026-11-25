"""Create and answer enquiries (T-2050, ADR-036)."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.leads.models import ConversationMessage, Lead, LeadActivity
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property
from apps.properties.public_listings import public_q, public_text

from .models import Enquiry

logger = logging.getLogger(__name__)

CONSENT_VERSION = '2026-11'
CONSENT_TEXT = ('Your name, phone number and message will be shared with the lister so they can answer you. '
                'You can stop sharing at any time.')
MAX_PER_DAY = 20
RENOTIFY = timedelta(hours=1)
USER, RESPONDER = 'user', 'responder'

_M = OrganizationMembership.Role
MANAGERS = (_M.OWNER, _M.ORG_ADMIN, _M.TEAM_MANAGER, _M.SALES_MANAGER, _M.CRM_OPERATOR)
_KINDS = {Property.ListingOwnerType.ORGANIZATION: Enquiry.Kind.ORGANIZATION,
          Property.ListingOwnerType.FREELANCE_AGENT: Enquiry.Kind.AGENT,
          Property.ListingOwnerType.CLIENT: Enquiry.Kind.OWNER}
_INTENTS = {'buy': Lead.Intent.BUY, 'rent': Lead.Intent.RENT, 'sell': Lead.Intent.SELL, 'invest': Lead.Intent.INVEST}


class EnquiryError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# ── Who may answer ────────────────────────────────────────────────────────────

def responder_q(user) -> Q:
    """Enquiries this user may answer, in the organization they are acting for."""
    from apps.core.permissions import get_active_membership
    q = Q(responder=user, kind__in=(Enquiry.Kind.AGENT, Enquiry.Kind.OWNER))
    if user.role == 'admin':
        q |= Q(kind=Enquiry.Kind.PLATFORM)
    m = get_active_membership(user)
    if m is not None:
        mine = Q(kind=Enquiry.Kind.ORGANIZATION, organization_id=m.organization_id)
        if m.role not in MANAGERS:
            mine &= (Q(lead__assigned_agent__user=user) if m.role != _M.VIEWER else Q(pk__in=[]))
        q |= mine
    return q


def recipients(e: Enquiry) -> list:
    """The people notified about a new message from the consumer."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if e.kind in (Enquiry.Kind.AGENT, Enquiry.Kind.OWNER):
        return [e.responder] if e.responder and e.responder.is_active else []
    if e.kind == Enquiry.Kind.PLATFORM:
        return list(User.objects.filter(role='admin', is_active=True).order_by('pk')[:10])
    members = OrganizationMembership.objects.filter(
        organization_id=e.organization_id, is_active=True, user__is_active=True).select_related('user')
    agent = e.lead.assigned_agent if e.lead else None
    if agent and agent.user_id:
        assigned = members.filter(user_id=agent.user_id).exclude(role=_M.VIEWER).first()
        if assigned:
            return [assigned.user]
    return [m.user for m in members.filter(role__in=MANAGERS).order_by('pk')[:10]]


def side_of(e: Enquiry, user) -> str | None:
    if e.user_id == user.pk:
        return USER
    return RESPONDER if Enquiry.objects.filter(responder_q(user), pk=e.pk).exists() else None


def visible_listing(listing_id, viewer_org=None):
    return (Property.objects.visible_to(viewer_org).filter(public_q(), pk=listing_id)
            .select_related('organization', 'listed_by_agent__user', 'owner').first())


def lister_name(p: Property) -> str:
    """Public name of who will receive the enquiry. Private people are not named."""
    if p.listing_owner_type == Property.ListingOwnerType.ORGANIZATION:
        return p.organization.name
    return {Enquiry.Kind.AGENT: 'the listing agent', Enquiry.Kind.OWNER: 'the property owner'}.get(
        _KINDS.get(p.listing_owner_type), 'the RealTron team')


# ── Create ────────────────────────────────────────────────────────────────────

def check_allowed(user, listing=None) -> None:
    """Raises EnquiryError. Also run before the assistant proposes an enquiry."""
    from apps.whatsapp.models import WhatsAppSession
    if not user.is_active or WhatsAppSession.objects.filter(
            user=user, conversation_mode=WhatsAppSession.ConversationMode.BLOCKED).exists():
        raise EnquiryError('Your account cannot send enquiries.', 'blocked')
    if listing is None:
        return
    agent = listing.listed_by_agent
    if (listing.owner_id == user.pk or (agent and agent.user_id == user.pk)
            or (listing.organization_id and OrganizationMembership.objects.filter(
                user=user, organization_id=listing.organization_id, is_active=True).exists())):
        raise EnquiryError('This is your own listing.', 'own_listing')
    kind = _KINDS.get(listing.listing_owner_type, Enquiry.Kind.PLATFORM)
    _check_blocks(user, listing, kind)
    if kind == Enquiry.Kind.ORGANIZATION and not listing.organization.is_active:
        raise EnquiryError('This lister is not available.', 'lister_unavailable')
    if kind == Enquiry.Kind.AGENT and not (agent.is_active and agent.user and agent.user.is_active):
        raise EnquiryError('This lister is not available.', 'lister_unavailable')
    if kind == Enquiry.Kind.OWNER and not (listing.owner and listing.owner.is_active):
        raise EnquiryError('This lister is not available.', 'lister_unavailable')


def _check_blocks(user, listing, kind) -> None:
    from .safety import blocked_sides
    sides = blocked_sides(user.pk, kind, listing.organization_id,
                          (agent.user_id if (agent := listing.listed_by_agent) else None)
                          if kind == Enquiry.Kind.AGENT else listing.owner_id)
    if USER in sides:
        raise EnquiryError('You blocked this lister. Unblock them first.', 'blocked_by_you')
    if RESPONDER in sides:                  # they do not learn why
        raise EnquiryError('This lister is not available.', 'lister_unavailable')


def _intent(listing, requirement) -> str:
    if listing is None:
        return _INTENTS.get(requirement.get('intent'), Lead.Intent.BUY)
    return Lead.Intent.BUY if listing.purpose == Property.Purpose.SALE else Lead.Intent.RENT


def _lead(user, kind, org, responder, listing, requirement, source) -> Lead:
    """
    The lead of this person at this lister: one per organization. Other kinds get
    a lead of their own, so it never mixes with the person's platform chats.
    """
    if org is not None:
        lead = Lead.objects.filter(user=user, organization=org).order_by('-created_at').first()
    else:
        lead = Lead.objects.filter(user=user, organization__isnull=True, enquiries__kind=kind,
                                   enquiries__responder=responder).order_by('-created_at').first()
    if lead is not None and lead.status == Lead.Status.BLOCKED_MALICIOUS:
        raise EnquiryError('Your account cannot send enquiries.', 'blocked')

    agent = listing.listed_by_agent if listing else None
    if org is not None and agent is not None and not (agent.user_id and OrganizationMembership.objects.filter(
            user_id=agent.user_id, organization=org, is_active=True).exists()):
        agent = None
    if agent is not None and not agent.is_active:
        agent = None
    city = (listing.city if listing else requirement.get('city', '')) or ''
    if lead is None:
        lead = Lead(user=user, organization=org, source=source, score=60, city_interest=city,
                    intent=_intent(listing, requirement))
    elif lead.status == Lead.Status.COLD:
        lead.status = Lead.Status.WARM
    lead.priority = max(lead.priority, 1)
    lead.assigned_agent = lead.assigned_agent or agent
    R = Lead.RoutingState
    lead.routing_state = (R.AGENT_ASSIGNED if lead.assigned_agent else R.ORG_QUEUE if org else R.AI_QUEUE)
    new = lead._state.adding
    lead.save()
    LeadActivity.objects.create(
        lead=lead, actor=user, action=LeadActivity.ActionType.CREATED if new else LeadActivity.ActionType.NOTE,
        notes='New enquiry', meta={'listing': listing.ref_no} if listing else {})
    return lead


def create(user, *, message: str, listing: Property | None = None, requirement: dict | None = None,
           preferred_channel: str = Enquiry.Channel.IN_APP, via: str = 'api',
           source: str = Lead.Source.WEB) -> tuple[Enquiry, bool]:
    """
    Record a consented enquiry. A second enquiry about the same listing continues
    the open thread. Returns ``(enquiry, created)``. The caller has the consent.
    """
    from django.contrib.auth import get_user_model
    requirement = requirement or {}
    check_allowed(user, listing)
    now = timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)     # one enquiry at a time per person
        if listing is not None:              # again, under the lock a block takes
            _check_blocks(user, listing, _KINDS.get(listing.listing_owner_type, Enquiry.Kind.PLATFORM))
        e = Enquiry.objects.filter(user=user, listing=listing, status=Enquiry.Status.OPEN).first()
        if e is not None:
            e.consented_at, e.consent_version, e.consent_via = now, CONSENT_VERSION, via
            e.consent_revoked_at, e.preferred_channel = None, preferred_channel
            if requirement:
                e.requirement = requirement
            e.save()
            post(e, user, USER, message)
            return e, False
        if Enquiry.objects.filter(user=user, created_at__gt=now - timedelta(days=1)).count() >= MAX_PER_DAY:
            raise EnquiryError('You have reached today\'s enquiry limit.', 'limit')
        kind = _KINDS.get(listing.listing_owner_type, Enquiry.Kind.PLATFORM) if listing else Enquiry.Kind.PLATFORM
        org = listing.organization if kind == Enquiry.Kind.ORGANIZATION else None
        responder = {Enquiry.Kind.AGENT: lambda: listing.listed_by_agent.user,
                     Enquiry.Kind.OWNER: lambda: listing.owner}.get(kind, lambda: None)()
        lead = _lead(user, kind, org, responder, listing, requirement, source)
        subject = public_text(listing, listing.title) if listing else 'Property request'
        e = Enquiry.objects.create(
            user=user, kind=kind, organization=org, responder=responder, listing=listing, subject=subject[:200],
            requirement=requirement, lead=lead, preferred_channel=preferred_channel,
            consent_version=CONSENT_VERSION, consent_via=via, consented_at=now, last_message_at=now)
        post(e, user, USER, message)
    logger.info('enquiry.created id=%s kind=%s via=%s', e.pk, kind, via)
    return e, True


# ── Thread ────────────────────────────────────────────────────────────────────

def post(e: Enquiry, sender, side: str, body: str, images=()) -> ConversationMessage:
    """Append a message and notify the other side (once per unread burst)."""
    with transaction.atomic():
        e = Enquiry.objects.select_for_update().get(pk=e.pk)
        if e.status != Enquiry.Status.OPEN:
            raise EnquiryError('This enquiry is closed.', 'closed')
        if images:
            from .safety import attach, check_files
            check_files(e, sender)
        if e.lead is None:                    # the lead was deleted: the thread needs a home
            e.lead = Lead.objects.create(user=e.user, organization=e.organization, source=Lead.Source.WEB)
        now = timezone.now()
        msg = ConversationMessage.objects.create(
            lead=e.lead, enquiry=e, sender=sender, body=body, channel=ConversationMessage.Channel.IN_APP,
            direction=(ConversationMessage.Direction.INBOUND if side == USER
                       else ConversationMessage.Direction.OUTBOUND))
        if images:
            attach(msg, sender, images)
        other = RESPONDER if side == USER else USER
        previous, read = getattr(e, f'{side}_last_at'), getattr(e, f'{other}_read_at')
        announce = previous is None or (read and read >= previous) or now - previous > RENOTIFY
        setattr(e, f'{side}_last_at', now)
        setattr(e, f'{side}_read_at', now)
        e.last_message_at = now
        e.save()
        if side == RESPONDER:
            Lead.objects.filter(pk=e.lead_id).update(last_contacted_at=now)
        if announce:
            _announce(e, other, first=previous is None)
    return msg


def _announce(e: Enquiry, to: str, first: bool) -> None:
    """No message text and no contact details: those stay behind the login."""
    from apps.notifications.services import notify_user
    if to == USER:
        title, people = 'New reply to your enquiry', [e.user]
    else:
        title, people = ('New enquiry' if first else 'New message'), recipients(e)
        if not people:
            logger.warning('enquiry.no_recipient id=%s kind=%s', e.pk, e.kind)
    for person in people:
        whatsapp = to == RESPONDER or e.preferred_channel == Enquiry.Channel.WHATSAPP
        email = to == RESPONDER or e.preferred_channel == Enquiry.Channel.EMAIL
        notify_user(person, title=title, message=f'{title}: {e.subject}. Open RealTron to read and answer.',
                    send_whatsapp=whatsapp, send_email=email, path=f'/threads/{e.pk}')


def mark_read(e: Enquiry, side: str) -> None:
    Enquiry.objects.filter(pk=e.pk).update(**{f'{side}_read_at': timezone.now()})


def set_sharing(e: Enquiry, side: str, share: bool) -> None:
    now = timezone.now()
    if side == USER:
        fields = ({'consent_revoked_at': None, 'consented_at': now, 'consent_version': CONSENT_VERSION,
                   'consent_via': 'api'} if share else {'consent_revoked_at': now})
    else:
        fields = {'responder_shared_at': now if share else None}
    Enquiry.objects.filter(pk=e.pk).update(**fields)
    e.refresh_from_db()


def close(e: Enquiry) -> None:
    Enquiry.objects.filter(pk=e.pk, status=Enquiry.Status.OPEN).update(
        status=Enquiry.Status.CLOSED, closed_at=timezone.now())
    e.refresh_from_db()


def counterpart(e: Enquiry, side: str) -> dict:
    """Who is on the other side. Phone (and private names) only while both share."""
    shared = e.contacts_shared
    if side == RESPONDER:
        return {'name': e.user.name if shared else None, 'phone': e.user.phone if shared else None}
    if e.kind == Enquiry.Kind.ORGANIZATION:
        agent = e.lead.assigned_agent if e.lead else None
        phone = (agent.contact_whatsapp if agent else '') or (e.organization.phone if e.organization else '')
        return {'name': e.organization.name if e.organization else None, 'phone': (phone or None) if shared else None}
    if e.kind == Enquiry.Kind.PLATFORM or not shared or e.responder is None:
        return {'name': 'RealTron' if e.kind == Enquiry.Kind.PLATFORM else None, 'phone': None}
    if e.kind == Enquiry.Kind.AGENT:
        agent = getattr(e.responder, 'agent_profile', None)
        if agent:
            return {'name': agent.name, 'phone': agent.contact_whatsapp}
    return {'name': e.responder.name, 'phone': e.responder.phone}


# ── Privacy ───────────────────────────────────────────────────────────────────

def erase_user(user) -> int:
    """Delete the person's enquiries with their messages; empty what they wrote in other threads."""
    mine = Enquiry.objects.filter(user=user)
    ConversationMessage.objects.filter(enquiry__in=mine).delete()
    n = mine.count()
    mine.delete()
    from .safety import erase_user as erase_safety
    erase_safety(user)
    ConversationMessage.objects.filter(enquiry__isnull=False, sender=user).update(body='[REDACTED]', sender=None)
    return n
