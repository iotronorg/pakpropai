"""
Unified contact and messages (T-3100, FR-CRM-001, GAP-016, ADR-064).

A **contact** is one person (``Lead.user``) as one organization knows them: all
their leads there (each a requirement), their WhatsApp chat with the
organization's number, and what the organization notes about them
(``CRMContact``: a name of its own and tags). There is no contact row to keep
in step: the list is read from the leads, and a ``CRMContact`` row is written
only when someone names or tags the person.

**Messages** are read from the ``unified_messages`` view over the CRM log
(``ConversationMessage``) and the WhatsApp log (``WhatsAppMessage``). What a
caller sees is decided here, with the rules that already applied:
- CRM rows of a lead: ``Lead.crm_messages()`` (never another lister's thread);
- WhatsApp rows: the person's sessions with the same organization's number
  (sessions not yet linked to the account are matched by phone, as erasure does).

The caller's leads (``visible``) decide which people they may open; an agent
sees the people whose leads are assigned to them, and the CRM rows of those leads.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils.dateparse import parse_datetime

from .models import ConversationMessage, CRMContact, Lead, UnifiedMessage

MAX_TAGS, MAX_TAG = 20, 40
OPEN = ~Q(routing_state=Lead.RoutingState.CLOSED)


class ContactError(Exception):
    def __init__(self, message: str, field: str = 'detail'):
        super().__init__(message)
        self.field = field


def _spellings(phone: str) -> set[str]:
    digits = (phone or '').lstrip('+')
    return {digits, '+' + digits} if digits else set()


# ── Messages ──────────────────────────────────────────────────────────────────

def sessions(user, organization_id):
    """The person's WhatsApp sessions with one organization's number (``None``: the platform number)."""
    from apps.whatsapp.models import WhatsAppSession
    who = Q(user=user) | Q(user__isnull=True, phone__in=_spellings(user.phone)) if user.phone else Q(user=user)
    where = Q(organization_id=organization_id) if organization_id else Q(organization__isnull=True)
    return WhatsAppSession.objects.filter(who, where)


def lead_messages(lead):
    """Everything said with the lead's person under the lead's organization: its CRM log and the WhatsApp chat."""
    return UnifiedMessage.objects.filter(
        Q(source='crm', row_id__in=lead.crm_messages().values('pk'))
        | Q(source='whatsapp', session_id__in=sessions(lead.user, lead.organization_id).values('pk')))


def contact_messages(org, user, leads):
    """The same for a contact: the CRM logs of ``leads`` (the person's leads the caller sees) and the chat."""
    crm = ConversationMessage.objects.filter(lead__in=leads).filter(
        Q(enquiry__isnull=True) | Q(enquiry__organization=org))
    return UnifiedMessage.objects.filter(
        Q(source='crm', row_id__in=crm.values('pk'))
        | Q(source='whatsapp', session_id__in=sessions(user, org.pk).values('pk')))


def _cursor(m) -> str:
    return base64.urlsafe_b64encode(f'{m.created_at.isoformat()}|{m.id}'.encode()).decode()


def _uncursor(raw: str) -> tuple[datetime, str]:
    try:
        at, mid = base64.urlsafe_b64decode(raw.encode()).decode().split('|', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ContactError('Not a cursor from this list.', 'before') from None
    at = parse_datetime(at)
    if at is None:
        raise ContactError('Not a cursor from this list.', 'before')
    return at, mid


def page(qs, *, before: str | None = None, limit: int = 50) -> tuple[list, str | None]:
    """Newest first; ``before`` is the ``next`` cursor of the previous page."""
    if before:
        at, mid = _uncursor(before)
        qs = qs.filter(Q(created_at__lt=at) | Q(created_at=at, id__lt=mid))
    rows = list(qs.order_by('-created_at', '-id')[:limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (_cursor(rows[-1]) if more and rows else None)


def message_dicts(rows) -> list[dict]:
    from django.contrib.auth import get_user_model
    ids = {m.sender_id for m in rows if m.sender_id}
    names = dict(get_user_model().objects.filter(pk__in=ids).values_list('pk', 'name')) if ids else {}
    out = []
    for m in rows:
        if m.direction == 'inbound':
            author = 'person'
        else:
            author = 'team' if m.sender_id else 'automated'      # automated: no sender recorded (assistant, system)
        out.append({'id': m.id, 'source': m.source, 'channel': m.channel, 'direction': m.direction,
                    'author': author, 'sender_name': names.get(m.sender_id) if m.sender_id else None,
                    'msg_type': m.msg_type, 'body': m.body, 'has_media': m.has_media,
                    'lead_id': str(m.lead_id) if m.lead_id else None,
                    'enquiry_id': str(m.enquiry_id) if m.enquiry_id else None, 'created_at': m.created_at})
    return out


# ── Contacts ──────────────────────────────────────────────────────────────────

def _notes(org, user_ids) -> dict:
    return {c.user_id: c for c in CRMContact.objects.filter(organization=org, user_id__in=list(user_ids))}


def people(visible, org, *, search: str = '', tag: str = ''):
    """One row per person among the caller's leads of ``org``, most recent activity first."""
    leads = visible.filter(organization=org)
    if tag:
        tagged = CRMContact.objects.filter(organization=org, tags__contains=[tag]).values('user_id')
        leads = leads.filter(user_id__in=tagged)
    if search:
        s = search.strip()
        digits = ''.join(ch for ch in s if ch.isdigit())
        named = CRMContact.objects.filter(organization=org, name__icontains=s).values('user_id')
        q = Q(user__name__icontains=s) | Q(user_id__in=named)
        if len(digits) >= 4:                      # a phone search never finds a person who withheld it
            q |= Q(user__phone__contains=digits, contact_withheld=False)
        leads = leads.filter(q)
    return (leads.order_by().values('user_id')
            .annotate(leads=Count('pk', distinct=True), open_leads=Count('pk', filter=OPEN, distinct=True),
                      shared=Count('pk', filter=Q(contact_withheld=False), distinct=True),
                      last_lead_at=Max('created_at'), last_contacted_at=Max('last_contacted_at'))
            .order_by('-last_lead_at', 'user_id'))


def people_dicts(org, rows) -> list[dict]:
    from django.contrib.auth import get_user_model
    ids = [r['user_id'] for r in rows]
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=ids).only('id', 'name', 'phone')}
    notes = _notes(org, ids)
    out = []
    for r in rows:
        u, n = users.get(r['user_id']), notes.get(r['user_id'])
        out.append({'id': str(r['user_id']), 'name': (n.name if n and n.name else None) or (u.name if u else None),
                    'phone': u.phone if u and r['shared'] else None, 'contact_withheld': not r['shared'],
                    'tags': n.tags if n else [], 'leads': r['leads'], 'open_leads': r['open_leads'],
                    'last_lead_at': r['last_lead_at'], 'last_contacted_at': r['last_contacted_at']})
    return out


def person_leads(visible, org, user_id):
    return visible.filter(organization=org, user_id=user_id)


def detail(visible, org, user) -> dict:
    from . import pipeline
    leads = list(person_leads(visible, org, user.pk).select_related('stage', 'assigned_agent')
                 .order_by('-created_at'))
    first = pipeline.first_stage(org)
    note = CRMContact.objects.filter(organization=org, user=user).first()
    shared = any(not lead.__dict__.get('contact_withheld', False) for lead in leads)
    return {
        'id': str(user.pk), 'name': (note.name if note and note.name else None) or user.name,
        'account_name': user.name, 'phone': user.phone if shared else None, 'contact_withheld': not shared,
        'tags': note.tags if note else [],
        'whatsapp': sessions(user, org.pk).exists(),
        'leads': [{
            'id': str(lead.pk), 'intent': lead.intent, 'status': lead.status,
            'stage': pipeline.stage_dict(pipeline.current(lead, first)), 'routing_state': lead.routing_state,
            'score': lead.score, 'location_interest': lead.city_interest, 'budget_min': lead.budget_min,
            'budget_max': lead.budget_max, 'budget_currency': lead.budget_currency, 'source': lead.source,
            'assigned_agent_id': lead.assigned_agent_id, 'assigned_agent_name':
                lead.assigned_agent.name if lead.assigned_agent_id else None,
            'last_contacted_at': lead.last_contacted_at, 'created_at': lead.created_at,
        } for lead in leads],
    }


def _tags(value) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise ContactError(f'A list of at most {MAX_TAGS} tags.', 'tags')
    out = []
    for t in value:
        t = ' '.join(str(t).split()) if isinstance(t, str) else ''
        if not t or len(t) > MAX_TAG:
            raise ContactError(f'Each tag 1 to {MAX_TAG} characters.', 'tags')
        if t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out


@transaction.atomic
def annotate(org, user, data: dict, *, actor=None) -> CRMContact:
    """Set the organization's name and tags for a person (``name: ''`` goes back to the account name)."""
    unknown = set(data) - {'name', 'tags', 'organization'}
    if unknown:
        raise ContactError(f'Unknown field(s): {", ".join(sorted(unknown))}.')
    note, _ = CRMContact.objects.select_for_update().get_or_create(
        organization=org, user=user, defaults={'source': CRMContact.Source.MANUAL})
    if 'name' in data:
        name = ' '.join(str(data['name'] or '').split()) if isinstance(data['name'], (str, type(None))) else None
        if name is None or len(name) > 100:
            raise ContactError('At most 100 characters.', 'name')
        note.name = name
    if 'tags' in data:
        note.tags = _tags(data['tags'])
    note.save()
    from apps.core.audit import safe_record
    safe_record('contact.updated', category='organization', actor=actor, organization=org, target=note,
                meta={'fields': sorted(set(data) - {'organization'}), 'tags': len(note.tags)})
    return note
