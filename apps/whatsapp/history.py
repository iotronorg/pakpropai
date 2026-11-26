"""
The one message log for WhatsApp (T-3100b, ADR-064 phase B).

Every WhatsApp message is written once, as a ``leads.ConversationMessage`` row
with its ``session`` (and the organization's lead of that person when there is
one): inbound messages and the assistant's replies (router), agent live chat
(consumer) and dashboard sends. ``WhatsAppMessage`` is no longer written; its
rows from before are read through the ``unified_messages`` view until
``manage.py backfill_contact_messages`` has copied them, and the table goes in
the contract phase (T-3100c).

Read a session's messages with ``session_log`` (both tables, no duplicates), so
the 24-hour window, the copilot and the live-chat history see old and new rows.
"""
from __future__ import annotations

import logging
import uuid

from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)


def crm_lead(user, org_id):
    """The lead of this chat's own organization: a chat is never filed under another lister (T-2050)."""
    from apps.leads.models import Lead
    if user is None:
        return None
    mine = Lead.objects.filter(user=user).order_by('-created_at')
    if org_id:
        return mine.filter(organization_id=org_id).first()
    return mine.filter(organization__isnull=True, enquiries__isnull=True).first()


def fallback_id(prefix: str) -> str:
    """An id for a send Meta gave none for (failed or buffered): unique, never matched by a webhook."""
    return f'{prefix}-{uuid.uuid4().hex}'


def record(session, *, direction: str, body: str, msg_type: str = 'text', wa_message_id: str = '',
           media_id: str = '', media_url: str = '', sender=None, lead=None, organization_id=None):
    """
    Write one WhatsApp message. ``session`` may be ``None`` for a dashboard send to a
    person with no session yet (then ``lead`` is required). A repeated inbound webhook
    (same Meta id in the same session) updates the row instead of adding one.
    """
    from apps.leads.models import ConversationMessage as CM
    org_id = organization_id if organization_id is not None else (
        session.organization_id if session is not None else getattr(lead, 'organization_id', None))
    if lead is None and session is not None:
        lead = crm_lead(session.user, session.organization_id) if session.user_id else None
    fields = dict(lead=lead, organization_id=org_id, direction=direction, channel=CM.Channel.WHATSAPP,
                  msg_type=msg_type or 'text', body=(body or '')[:4096], media_id=media_id or '',
                  media_url=media_url or '', sender=sender)
    if session is None or not wa_message_id:          # a failed send has no id: nothing to match
        return CM.objects.create(session=session, wa_message_id=wa_message_id or '', **fields)
    try:
        with transaction.atomic():
            if direction == CM.Direction.INBOUND:
                row, _ = CM.objects.update_or_create(session=session, wa_message_id=wa_message_id, defaults=fields)
            else:
                row, _ = CM.objects.get_or_create(session=session, wa_message_id=wa_message_id, defaults=fields)
    except IntegrityError:                         # the same webhook processed twice at once
        row = CM.objects.get(session=session, wa_message_id=wa_message_id)
    return row


def session_log(session):
    """A session's messages from both logs (``UnifiedMessage``; ``row_id`` is the stored row's id)."""
    from apps.leads.models import UnifiedMessage
    return UnifiedMessage.objects.filter(session_id=getattr(session, 'pk', session))


def sessions_log(sessions):
    from apps.leads.models import UnifiedMessage
    return UnifiedMessage.objects.filter(session_id__in=sessions.values('pk'))
