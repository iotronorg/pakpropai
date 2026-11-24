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
            'You can change or cancel it in RealTron.')
    return {'success': True, 'appointment_id': str(appt.pk), 'message': text, 'whatsapp_summary': text}
