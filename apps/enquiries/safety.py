"""Attachments, blocking, reports and message warnings in threads (T-2051, ADR-045, FR-COM-001/007)."""
from __future__ import annotations

import base64
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.core.images import CONTENT_TYPES, ImageRejected, sanitize_image

from .models import Enquiry, EnquiryBlock, MessageAttachment, ThreadReport
from .services import USER, EnquiryError

logger = logging.getLogger(__name__)

MAX_FILES = 3
MAX_INPUT = 5 * 1024 * 1024
MAX_STORED = 2 * 1024 * 1024
MAX_SIDE = 1600
MAX_FILES_PER_DAY = 30
MAX_REPORTS_PER_DAY = 10
PHOTO = '[photo]'

# ── Warnings shown to the reader of a message ────────────────────────────────

_LINK = re.compile(r'https?://|\bwww\.|\b(?:bit\.ly|tinyurl\.com|t\.me|wa\.me)/', re.I)
_MONEY = re.compile(
    r'\b(?:advance|token|deposit|bayana|biyana|booking)\b.{0,60}\b(?:send|transfer|pay|bhej\w*|jazz\s?cash|easy\s?paisa|'
    r'western\s+union|crypto|bitcoin|usdt|gift\s+cards?)\b'
    r'|\b(?:send|transfer|pay|bhej\w*)\b.{0,60}\b(?:advance|token|deposit|bayana|biyana|jazz\s?cash|easy\s?paisa|western\s+union|'
    r'crypto|bitcoin|usdt|gift\s+cards?)\b'
    r'|(?:بیعانہ|ایڈوانس|ٹوکن).{0,60}(?:بھیج|ٹرانسفر|جمع)', re.I | re.S)
_CODE = re.compile(
    r'\b(?:otp|one[\s-]?time\s+(?:code|password)|verification\s+code|security\s+code|cvv|atm\s+pin|pin\s+code\s+of\s+(?:your|the)\s+card)\b'
    r'|او\s?ٹی\s?پی|تصدیقی\s+کوڈ', re.I)


def warnings(text: str) -> list[str]:
    """Codes for the client to explain. A warning never hides or stops a message."""
    return [code for code, pattern in (('external_link', _LINK), ('payment_request', _MONEY),
                                       ('verification_code', _CODE)) if pattern.search(text or '')]


# ── Attachments ──────────────────────────────────────────────────────────────

def clean_files(files) -> list:
    """Sanitised images, before any database work."""
    if len(files) > MAX_FILES:
        raise EnquiryError(f'At most {MAX_FILES} photos per message.', 'attachment')
    out = []
    for f in files:
        try:
            image = sanitize_image(f, max_bytes=MAX_INPUT, max_side=MAX_SIDE)
        except ImageRejected as exc:
            raise EnquiryError(str(exc), 'attachment') from None
        if image.size > MAX_STORED:
            raise EnquiryError('The photo is too large. Send it as JPEG.', 'attachment')
        out.append(image)
    return out


def check_files(e: Enquiry, sender) -> None:
    """
    Inside the posting transaction. A photo can show a phone number, so photos are
    possible only while both sides share their contact details.
    """
    if not e.contacts_shared:
        raise EnquiryError('Photos can be sent once both sides share their contact details.', 'not_shared')
    get_user_model().objects.select_for_update().get(pk=sender.pk)
    if MessageAttachment.objects.filter(sender=sender, created_at__gt=timezone.now() - timedelta(days=1)
                                        ).count() >= MAX_FILES_PER_DAY:
        raise EnquiryError('You have reached today\'s photo limit.', 'limit')


def attach(message, sender, images) -> None:
    MessageAttachment.objects.bulk_create(MessageAttachment(
        message=message, sender=sender, content_type=i.content_type, size=i.size, width=i.width, height=i.height,
        payload=base64.b64encode(i.content.read()).decode()) for i in images)


def attachment_for(e: Enquiry, side: str, pk) -> MessageAttachment | None:
    """Own photos always; the other side's only while both share."""
    a = MessageAttachment.objects.filter(pk=pk, message__enquiry=e).select_related('message').first()
    if a is None or a.content_type not in CONTENT_TYPES.values():
        return None
    mine = (a.message.direction == 'inbound') == (side == USER)
    return a if mine or e.contacts_shared else None


def attachment_meta():
    """Prefetch without the photo itself."""
    return Prefetch('attachments', MessageAttachment.objects.defer('payload'))


# ── Blocking ─────────────────────────────────────────────────────────────────

def _lister(e_or_kind, organization_id=None, responder_id=None) -> dict | None:
    """Filter for the lister. None: the platform team, or the lister's account is gone."""
    K = Enquiry.Kind
    if e_or_kind == K.ORGANIZATION and organization_id:
        return {'organization_id': organization_id, 'responder_id': None}
    if e_or_kind in (K.AGENT, K.OWNER) and responder_id:
        return {'organization_id': None, 'responder_id': responder_id}
    return None


def pair(e: Enquiry) -> dict | None:
    lister = _lister(e.kind, e.organization_id, e.responder_id)
    return {'user_id': e.user_id, **lister} if lister else None


def blocked_sides(user_id, kind, organization_id, responder_id) -> set[str]:
    lister = _lister(kind, organization_id, responder_id)
    if lister is None:
        return set()
    return set(EnquiryBlock.objects.filter(user_id=user_id, **lister).values_list('side', flat=True))


def block(e: Enquiry, actor, side: str) -> None:
    """Close every thread of the two and stop new ones. The blocker stops sharing their details."""
    who = pair(e)
    if who is None:
        raise EnquiryError('This side cannot be blocked. Close the thread instead.', 'cannot_block')
    now = timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=e.user_id)       # against a new enquiry at the same time
        EnquiryBlock.objects.get_or_create(**who, side=side, defaults={'created_by': actor})
        threads = Enquiry.objects.filter(**who)
        if side == USER:
            threads.filter(consent_revoked_at__isnull=True).update(consent_revoked_at=now)
        else:
            threads.update(responder_shared_at=None)
        threads.filter(status=Enquiry.Status.OPEN).update(status=Enquiry.Status.CLOSED, closed_at=now)
    logger.info('enquiry.blocked id=%s side=%s', e.pk, side)


def unblock(e: Enquiry, side: str) -> None:
    """New enquiries are possible again; closed threads stay closed."""
    if (who := pair(e)) is not None:
        EnquiryBlock.objects.filter(**who, side=side).delete()


# ── Reports ──────────────────────────────────────────────────────────────────

def report(e: Enquiry, reporter, side: str, category: str, text: str = '') -> ThreadReport:
    """A moderator may then read this thread. Never closes it by itself."""
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=reporter.pk)
        day = timezone.now() - timedelta(days=1)
        existing = ThreadReport.objects.filter(enquiry=e, reporter=reporter, status=ThreadReport.Status.OPEN).first()
        if existing is None and ThreadReport.objects.filter(
                reporter=reporter, created_at__gt=day).count() >= MAX_REPORTS_PER_DAY:
            raise EnquiryError('You have reached today\'s limit.', 'limit')
        r = existing or ThreadReport(enquiry=e, reporter=reporter, side=side, due_at=timezone.now() + timedelta(
            hours=settings.MODERATION_SLA_HOURS))
        r.category, r.text = category, text
        r.priority = 2 if category == ThreadReport.Category.FRAUD else 0
        r.save()
    logger.info('enquiry.reported id=%s side=%s category=%s', e.pk, side, category)
    return r


def is_party(e: Enquiry, user) -> bool:
    """Took part, or works for the organization: may not judge the report."""
    from apps.organizations.models import OrganizationMembership
    return (user.pk in (e.user_id, e.responder_id) or e.messages.filter(sender=user).exists()
            or bool(e.organization_id and OrganizationMembership.objects.filter(
                user=user, organization_id=e.organization_id, is_active=True).exists()))


def decide(pk, moderator, decision: str, note: str = '') -> ThreadReport:
    """``dismiss``, or ``close`` (the thread is closed)."""
    from apps.notifications.services import notify_user

    from .services import close
    with transaction.atomic():
        r = ThreadReport.objects.select_for_update().get(pk=pk)
        if r.status != ThreadReport.Status.OPEN:
            raise EnquiryError('This report was already decided.', 'decided')
        if r.reporter_id == moderator.pk or is_party(r.enquiry, moderator):
            raise EnquiryError('You are part of this thread.', 'own_thread')
        r.status = ThreadReport.Status.CLOSED if decision == 'close' else ThreadReport.Status.DISMISSED
        r.decided_by, r.decided_at, r.note = moderator, timezone.now(), note
        r.save()
        if decision == 'close':
            close(r.enquiry)
        if r.reporter and r.reporter.is_active:
            notify_user(r.reporter, title='Your report was reviewed', message='Thank you. We reviewed your report.',
                        send_whatsapp=False, send_email=False, path=f'/threads/{r.enquiry_id}')
    return r


def erase_user(user) -> None:
    MessageAttachment.objects.filter(sender=user).delete()
    ThreadReport.objects.filter(reporter=user).update(text='[REDACTED]', reporter=None)
    EnquiryBlock.objects.filter(Q(user=user) | Q(responder=user)).delete()
