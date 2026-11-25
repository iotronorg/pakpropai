"""Who needs review, the queue, decisions, reports and appeals (T-2014, ADR-040)."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.properties.models import ListingTransition, Property

from . import checks
from .models import ModerationItem, UserReport

logger = logging.getLogger(__name__)
I, S, R = ModerationItem.Status, Property.Status, ModerationItem.Reason
MODERATOR_ROLES = ('super_admin', 'ops_admin', 'compliance_admin')
HIGH, NORMAL = 2, 0
MANY_REPORTERS = 3
MAX_REPORTS_PER_DAY = 20


class ModerationError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def is_moderator(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.role == 'admin'
                and user.platform_role in MODERATOR_ROLES)


# ── Is a review needed? ───────────────────────────────────────────────────────

def digest(p: Property) -> str:
    """The content a moderator judges. The price is watched by its own check."""
    parts = [p.title, p.description, p.city, p.location, p.purpose, p.category, p.subtype,
             f'{float(p.size_value):g}' if p.size_value is not None else None, p.country]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def _approval(p: Property) -> ModerationItem | None:
    """The last approval, if what it approved is still what the listing says."""
    last = (ModerationItem.objects.filter(listing_id=p.pk, status__in=(I.APPROVED, I.REJECTED, I.SUSPENDED))
            .order_by('-decided_at').first())
    return last if last and last.status == I.APPROVED and last.content_digest == digest(p) else None


def taken_down(p: Property) -> bool:
    """Suspended and not reinstated by staff since (a lister can only archive it)."""
    last = (ListingTransition.objects.filter(property_id=p.pk)
            .filter(to_status__in=(S.SUSPENDED, S.PUBLISHED)).order_by('-created_at', '-id').first())
    if last is None or last.to_status != S.SUSPENDED:
        return False
    after = ListingTransition.objects.filter(property_id=p.pk, id__gt=last.pk, from_status=S.SUSPENDED).first()
    return after is None or after.to_status == S.ARCHIVED


def needs_review(p: Property) -> bool:
    """For a fresh publication by someone who is not staff."""
    if taken_down(p):
        return True
    approved = _approval(p)
    if approved is None and p.listing_owner_type == Property.ListingOwnerType.CLIENT:
        return True
    return bool(set(checks.run(p)) - set(approved.flags if approved else ()))


# ── Queue ─────────────────────────────────────────────────────────────────────

def open_item(p: Property, reason: str, *, priority: int = NORMAL, flags=None, appeal_text: str = '') -> ModerationItem:
    """The one open item of a listing; a second cause raises it, never hides it."""
    with transaction.atomic():
        Property.objects.select_for_update().only('pk').get(pk=p.pk)
        item = ModerationItem.objects.filter(listing_id=p.pk, status=I.OPEN).first()
        flags = checks.run(p) if flags is None else list(flags)
        if item is None:
            return ModerationItem.objects.create(
                listing=p, reason=reason, flags=flags, priority=priority or (1 if flags else NORMAL),
                appeal_text=appeal_text, due_at=timezone.now() + timedelta(hours=settings.MODERATION_SLA_HOURS))
        item.flags = sorted({*item.flags, *flags})
        item.priority = max(item.priority, priority)
        item.appeal_text = appeal_text or item.appeal_text
        item.save(update_fields=['flags', 'priority', 'appeal_text'])
        return item


def on_transition(t: ListingTransition) -> None:
    """Called for every status change, whoever made it."""
    p = t.property
    if t.to_status == S.PENDING_REVIEW:
        open_item(p, R.REVIEW)
        return
    item = ModerationItem.objects.filter(listing_id=p.pk, status=I.OPEN).first()
    if item is None:
        return
    if t.to_status == S.PUBLISHED and t.from_status in (S.PENDING_REVIEW, S.SUSPENDED):     # e.g. Django admin
        _close(item, I.APPROVED, by=t.actor, listing=p)
    elif t.to_status == S.SUSPENDED:
        _close(item, I.SUSPENDED, by=t.actor, listing=p)
    elif t.from_status in (S.PENDING_REVIEW, S.SUSPENDED) and not item.reports.exists():
        _close(item, I.WITHDRAWN, by=t.actor, listing=p)


def after_edit(p: Property, actor) -> None:
    """A published listing was changed by its lister: new flags hide it, private text edits are queued."""
    from apps.properties.lifecycle import is_staff, transition
    if is_staff(actor) or p.status not in (S.PUBLISHED, S.UNDER_OFFER):
        return
    approved = (ModerationItem.objects.filter(listing_id=p.pk, status=I.APPROVED).order_by('-decided_at').first())
    flags = sorted(set(checks.run(p)) - set(approved.flags if approved else ()))
    if flags and p.status == S.PUBLISHED:
        transition(p, S.PENDING_REVIEW, reason='Edited; waiting for review.',
                   source=ListingTransition.Source.SYSTEM, system=True)
    elif flags or (p.listing_owner_type == Property.ListingOwnerType.CLIENT
                   and not (approved and approved.content_digest == digest(p))):
        open_item(p, R.EDITED, flags=flags)


# ── Decisions ─────────────────────────────────────────────────────────────────

_TARGET = {'approve': (S.PUBLISHED, I.APPROVED), 'reject': (S.DRAFT, I.REJECTED),
           'suspend': (S.SUSPENDED, I.SUSPENDED), 'dismiss': (None, I.DISMISSED)}


def decide(item_id, moderator, decision: str, *, seen: str, reason_code: str = '', note: str = '') -> ModerationItem:
    """``seen`` is the digest the moderator looked at; changed content must be read again."""
    from apps.properties.lifecycle import TransitionError, can_manage, transition
    to, status = _TARGET[decision]
    with transaction.atomic():
        item = ModerationItem.objects.select_for_update().get(pk=item_id)
        if item.status != I.OPEN:
            raise ModerationError('This item was already decided.', 'decided')
        p = Property.objects.select_for_update().get(pk=item.listing_id)
        if p.owner_id == moderator.pk or (p.listed_by_agent_id and p.listed_by_agent.user_id == moderator.pk):
            raise ModerationError('You cannot decide on your own listing.', 'own_listing')
        if not can_manage(moderator, p):
            raise ModerationError('Only moderators can decide.', 'forbidden')
        if seen != digest(p):
            raise ModerationError('The listing was changed. Read it again.', 'changed')
        if decision == 'approve':
            if p.status in (S.PUBLISHED, S.UNDER_OFFER):
                to = None
            elif p.status not in (S.PENDING_REVIEW, S.SUSPENDED):
                raise ModerationError('The lister withdrew this listing.', 'not_waiting')
        elif decision == 'reject' and p.status != S.PENDING_REVIEW:
            raise ModerationError('Only a listing waiting for review can be returned. Suspend it instead.', 'not_waiting')
        elif decision == 'dismiss' and p.status == S.PENDING_REVIEW:
            raise ModerationError('Approve or reject a listing that waits for review.', 'waiting')
        elif decision == 'suspend' and p.status == S.SUSPENDED:
            to = None
        if status == I.APPROVED:
            item.flags = checks.run(p)            # what was accepted
        _close(item, status, by=moderator, listing=p, reason_code=reason_code, note=note)
        if to is not None:
            try:
                transition(p, to, actor=moderator, reason=(f'{reason_code}: {note}' if reason_code else note)[:500],
                           source=ListingTransition.Source.API)
            except TransitionError as exc:
                raise ModerationError(str(exc), 'transition') from exc
        if to is not None or item.reason == R.APPEAL:
            _tell_lister(p, item)
    logger.info('moderation.decided item=%s decision=%s by=%s', item.pk, decision, moderator.pk)
    return item


def _close(item, status, *, by, listing, reason_code='', note='') -> None:
    item.status, item.decided_by, item.decided_at = status, by, timezone.now()
    item.content_digest, item.reason_code, item.note = digest(listing), reason_code, note
    item.save()


def lister(p: Property):
    agent = p.listed_by_agent
    return p.owner or (agent.user if agent else None)


def _tell_lister(p: Property, item: ModerationItem) -> None:
    from apps.notifications.services import notify_user
    person = lister(p)
    if person is None:
        return
    what = {I.APPROVED: 'is now public.', I.REJECTED: 'was returned to you. Please correct it and submit it again.',
            I.SUSPENDED: 'was taken down. You can appeal once.', I.DISMISSED: 'stays as it is.'}[item.status]
    why = f' Reason: {item.get_reason_code_display()}.' if item.reason_code else ''
    notify_user(person, title='Listing review', message=f'Your listing {p.ref_no} {what}{why} {item.note}'.strip(),
                send_email=True, path=f'/listings/{p.pk}')


# ── Reports and appeals ───────────────────────────────────────────────────────

def report(user, p: Property, category: str, text: str = '') -> UserReport:
    """Never hides a listing by itself: reports can be abused."""
    from apps.properties.lifecycle import can_manage
    from django.contrib.auth import get_user_model
    if can_manage(user, p):
        raise ModerationError('You manage this listing.', 'own_listing')
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        if UserReport.objects.filter(reporter=user,
                                     created_at__gt=timezone.now() - timedelta(days=1)).count() >= MAX_REPORTS_PER_DAY:
            raise ModerationError('You have reached today\'s limit.', 'limit')
        item = open_item(p, R.REPORTED, flags=[])
        rep, _ = UserReport.objects.update_or_create(
            reporter=user, item=item, defaults={'listing': p, 'category': category, 'text': text})
        if category == UserReport.Category.FRAUD or item.reports.count() >= MANY_REPORTERS:
            ModerationItem.objects.filter(pk=item.pk, priority__lt=HIGH).update(priority=HIGH)
    return rep


def appeal(user, p: Property, text: str) -> ModerationItem:
    """One appeal per suspension."""
    from apps.properties.lifecycle import can_manage
    if not can_manage(user, p):
        raise ModerationError('You do not manage this listing.', 'forbidden')
    with transaction.atomic():
        p = Property.objects.select_for_update().get(pk=p.pk)
        if p.status != S.SUSPENDED:
            raise ModerationError('Only a suspended listing can be appealed.', 'not_suspended')
        since = (ListingTransition.objects.filter(property=p, to_status=S.SUSPENDED)
                 .order_by('-created_at', '-id').values_list('created_at', flat=True).first())
        mine = ModerationItem.objects.filter(listing=p).exclude(appeal_text='')
        if since is not None and mine.filter(created_at__gte=since).exists() or mine.filter(status=I.OPEN).exists():
            raise ModerationError('This suspension was already appealed.', 'appealed')
        return open_item(p, R.APPEAL, priority=1, flags=[], appeal_text=text)


def notify_overdue() -> int:
    """Tell moderators once about items past their deadline."""
    from django.contrib.auth import get_user_model

    from apps.notifications.services import notify_user
    now = timezone.now()
    ids = list(ModerationItem.objects.filter(status=I.OPEN, due_at__lt=now, overdue_notified_at__isnull=True)
               .values_list('pk', flat=True)[:500])
    n = ModerationItem.objects.filter(pk__in=ids, overdue_notified_at__isnull=True).update(overdue_notified_at=now)
    from apps.enquiries.models import ThreadReport
    n += ThreadReport.objects.filter(status=ThreadReport.Status.OPEN, due_at__lt=now,
                                     overdue_notified_at__isnull=True).update(overdue_notified_at=now)
    if n:
        for person in get_user_model().objects.filter(role='admin', is_active=True,
                                                      platform_role__in=MODERATOR_ROLES).order_by('pk')[:10]:
            notify_user(person, title='Moderation overdue', message=f'{n} item(s) passed the review deadline.',
                        send_whatsapp=False, send_email=True, path='/moderation')
    return n


def erase_user(user) -> int:
    ModerationItem.objects.filter(listing__owner=user).exclude(appeal_text='').update(appeal_text='[REDACTED]')
    return UserReport.objects.filter(reporter=user).update(text='[REDACTED]', reporter=None)
