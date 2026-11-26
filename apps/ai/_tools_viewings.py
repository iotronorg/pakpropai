"""Viewing tools (T-2060). book_viewing runs only after the user confirmed (T-2012)."""
import logging
from datetime import date, datetime, timedelta, timezone as dt_tz

from django.utils import timezone

from ._tools_context import _ctx_conversation, _ctx_org, _ctx_user
from .confirmation import guarded

logger = logging.getLogger(__name__)
SHOWN = 12


def resolve(listing: str):
    """A bookable listing from an id, reference or position, or None."""
    from apps.enquiries.services import visible_listing
    from apps.properties.listing_search import find

    from ._tools_listings import _state
    org, state = _ctx_org.get(), _state()
    hit = find(listing, org, (state.last_results or []) if state else [])
    return visible_listing(hit.pk, org) if hit else None


def parse_start(text: str, tz) -> datetime:
    """ISO time; without an offset it is local time at the property."""
    value = datetime.fromisoformat(str(text).strip().replace('Z', '+00:00'))
    return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(dt_tz.utc)


def find_viewing_slots(listing: str, from_date: str = '', days: int = 7) -> dict:
    """
    Free times to visit one listing. Call it when the user wants to see a property, then let the user choose a time.

    Args:
        listing: Listing id, reference number (RT-...), or its position in the last results, e.g. '2'
        from_date: First day as YYYY-MM-DD. Empty = today
        days: Number of days to look at, 1 to 14
    """
    from apps.viewings import services as V
    try:
        user = _ctx_user.get()
        if user is None:
            return {'slots': [], 'error': 'The user must sign in or register on WhatsApp first.'}
        p = resolve(listing)
        if p is None:
            return {'slots': [], 'error': 'Listing not found or no longer available.'}
        tz = V.listing_timezone(p)
        today = timezone.now().astimezone(tz).date()
        try:
            first = max(date.fromisoformat(from_date), today) if from_date else today
        except ValueError:
            first = today
        start = datetime.combine(first, datetime.min.time(), tzinfo=tz)
        rows = V.slots(p, start, start + timedelta(days=min(max(days, 1), V.MAX_WINDOW_DAYS)), user)
        out = {'listing_id': str(p.pk), 'today': today.isoformat(), 'timezone': tz.key, 'more': len(rows) > SHOWN,
               'slots': [{'start': s.start.astimezone(tz).strftime('%Y-%m-%dT%H:%M'),
                          'shown_as': s.start.astimezone(tz).strftime('%a %d %b, %H:%M')} for s in rows[:SHOWN]]}
        out['instruction'] = ('Offer only these times. Call book_viewing with the chosen start.' if rows else
                              'No free times online. Offer another week, or create_enquiry to ask the lister.')
        return out
    except Exception as exc:
        logger.error('find_viewing_slots failed: %s', exc, exc_info=True)
        return {'slots': [], 'error': 'Viewing times are temporarily unavailable.'}


@guarded
def book_viewing(listing: str, start: str, note: str = '') -> dict:
    """
    Book a visit at a time returned by find_viewing_slots.

    Args:
        listing: Listing id, reference number (RT-...), or its position in the last results
        start: The chosen start exactly as find_viewing_slots returned it, e.g. '2027-01-30T15:00'
        note: Message for the agent, only if the user gave one
    """
    from apps.enquiries import services as E
    from apps.leads.models import Lead
    from apps.viewings import services as V
    user, conversation = _ctx_user.get(), _ctx_conversation.get()
    p = E.visible_listing(listing, _ctx_org.get())
    if user is None or p is None:
        return {'success': False, 'error': 'This listing is no longer available.'}
    tz = V.listing_timezone(p)
    whatsapp = conversation is None or conversation.channel == 'whatsapp'
    try:
        appt = V.book(user, p, parse_start(start, tz), note=note, via='assistant',
                      preferred_channel='whatsapp' if whatsapp else 'in_app',
                      source=Lead.Source.WHATSAPP if whatsapp else Lead.Source.WEB)
    except (V.ViewingError, E.EnquiryError) as exc:
        return {'success': False, 'error': str(exc)}
    text = (f'✅ Your viewing is booked for {V.local_text(appt.scheduled_at, tz)}. The agent will confirm it. '
            f'{V.CHANGE_TEXT}')
    return {'success': True, 'appointment_id': str(appt.pk), 'message': text, 'whatsapp_summary': text}


# ── The visitor's own viewings (KI-076) ───────────────────────────────────────


def upcoming(user, org=None) -> list:
    """
    The user's viewings that can still be changed, soonest first. In an
    organization's chat only that organization's viewings (ADR-036: a chat never
    uses another organization's lead).
    """
    from apps.leads.models import Appointment
    from apps.viewings import services as V
    if user is None or not getattr(user, 'pk', None):
        return []
    qs = Appointment.objects.filter(lead__user=user, status__in=V.ACTIVE, scheduled_at__gt=timezone.now())
    if org is not None:
        qs = qs.filter(lead__organization=org)
    return list(qs.select_related('property', 'agent', 'lead__organization').order_by('scheduled_at', 'pk')[:SHOWN])


def find_viewing(ref: str, user, org=None):
    """One of :func:`upcoming` by position ('1'), viewing id, or the listing's id or reference."""
    import re
    import uuid
    rows, ref = upcoming(user, org), str(ref or '').strip()
    if re.fullmatch(r'#?\d{1,2}', ref):
        n = int(ref.lstrip('#'))
        return rows[n - 1] if 1 <= n <= len(rows) else None
    try:
        key = uuid.UUID(ref)
        return next((a for a in rows if key in (a.pk, a.property_id)), None)
    except ValueError:
        return next((a for a in rows if a.property and (a.property.ref_no or '').lower() == ref.lower()), None)


def viewing_text(appt) -> str:
    from apps.properties.public_listings import public_text
    from apps.viewings import services as V
    p = appt.property
    what = f'{public_text(p, p.title)} ({p.ref_no})' if p else 'Viewing'
    return f'{what}, {V.local_text(appt.scheduled_at, V.zone(appt))}'


def my_viewings() -> dict:
    """
    The user's upcoming viewings. Call it when the user asks about their viewings or wants to cancel or move one.
    """
    from apps.properties.public_listings import public_text
    from apps.viewings import services as V
    try:
        user = _ctx_user.get()
        if user is None:
            return {'viewings': [], 'error': 'The user must sign in or register on WhatsApp first.'}
        out = []
        for n, a in enumerate(upcoming(user, _ctx_org.get()), 1):
            tz, p = V.zone(a), a.property
            out.append({'position': n, 'viewing_id': str(a.pk), 'status': a.status,
                        'listing': p.ref_no if p else '', 'title': public_text(p, p.title) if p else '',
                        'start': a.scheduled_at.astimezone(tz).strftime('%Y-%m-%dT%H:%M'),
                        'shown_as': V.local_text(a.scheduled_at, tz)})
        return {'viewings': out, 'instruction': (
            'To cancel: cancel_viewing. To move: find_viewing_slots for the same listing, then reschedule_viewing.'
            if out else 'No upcoming viewings here. Offer to find a listing to visit.')}
    except Exception as exc:
        logger.error('my_viewings failed: %s', exc, exc_info=True)
        return {'viewings': [], 'error': 'Viewings are temporarily unavailable.'}


@guarded
def cancel_viewing(viewing: str) -> dict:
    """
    Cancel one of the user's upcoming viewings.

    Args:
        viewing: Its position in my_viewings ('1'), its viewing_id, or the listing's reference number
    """
    from apps.viewings import services as V
    appt = find_viewing(viewing, _ctx_user.get(), _ctx_org.get())
    if appt is None:
        return {'success': False, 'error': 'This viewing can no longer be changed.'}
    try:
        V.cancel(appt)
    except V.ViewingError as exc:
        return {'success': False, 'error': str(exc)}
    text = f'✅ Your viewing is cancelled: {viewing_text(appt)}. The agent has been told.'
    return {'success': True, 'appointment_id': str(appt.pk), 'message': text, 'whatsapp_summary': text}


@guarded
def reschedule_viewing(viewing: str, start: str) -> dict:
    """
    Move one of the user's upcoming viewings to a free time of the same listing.

    Args:
        viewing: Its position in my_viewings ('1'), its viewing_id, or the listing's reference number
        start: The new start exactly as find_viewing_slots returned it for this listing, e.g. '2027-01-30T15:00'
    """
    from apps.viewings import services as V
    user = _ctx_user.get()
    appt = find_viewing(viewing, user, _ctx_org.get())
    if appt is None:
        return {'success': False, 'error': 'This viewing can no longer be changed.'}
    try:
        appt = V.reschedule(appt, user, parse_start(start, V.zone(appt)))
    except V.ViewingError as exc:
        return {'success': False, 'error': str(exc)}
    except (ValueError, OverflowError):
        return {'success': False, 'error': 'That time is not free. Call find_viewing_slots and offer its times.'}
    text = f'✅ Your viewing is moved: {viewing_text(appt)}. The agent will confirm it. {V.CHANGE_TEXT}'
    return {'success': True, 'appointment_id': str(appt.pk), 'message': text, 'whatsapp_summary': text}
