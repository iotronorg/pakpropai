"""
save_listing, create_saved_search and remember_preferences (T-2040). The last two
run only after confirmation, on the criteria frozen when they were proposed.
"""
import json
import logging

from django.db import transaction

from ._tools_context import _ctx_user
from .confirmation import guarded

logger = logging.getLogger(__name__)


def save_listing(listing: str) -> dict:
    """
    Add a listing to the user's favourites. Call it when the user says to save, keep or shortlist a listing.

    Args:
        listing: Listing id, reference number (RT-...), or its position in the last results, e.g. '2'
    """
    from apps.preferences import services as S

    from ._tools_viewings import resolve
    try:
        user = _ctx_user.get()
        if user is None:
            return {'saved': False, 'error': 'The user must sign in or register on WhatsApp first.'}
        p = resolve(listing)
        if p is None:
            return {'saved': False, 'error': 'Listing not found or no longer available.'}
        S.keep(user, p)
        return {'saved': True, 'listing_id': str(p.pk)}
    except S.PreferenceError as exc:
        return {'saved': False, 'error': str(exc)}
    except Exception as exc:
        logger.error('save_listing failed: %s', exc, exc_info=True)
        return {'saved': False, 'error': 'The listing could not be saved.'}


@guarded
def create_saved_search(frequency: str = 'daily', channel: str = 'whatsapp', criteria: str = '') -> dict:
    """
    Alert the user about new listings for their current search. Search first, then call it when the user wants to be notified.

    Args:
        frequency: 'instant', 'daily' or 'weekly'
        channel: 'whatsapp', 'email' or 'in_app'
        criteria: Leave empty; the current search is used
    """
    from apps.preferences import alerts as A
    from apps.preferences.services import PreferenceError
    from apps.properties import criteria as C
    try:
        s = A.create(_ctx_user.get(), json.loads(criteria), frequency=frequency, channel=channel, via='assistant')
    except (C.CriteriaInvalid, C.LocationAmbiguous):
        return {'success': False, 'error': 'This search is no longer possible.'}
    except PreferenceError as exc:
        return {'success': False, 'error': str(exc)}
    text = f'✅ Saved: “{s.name}”. I will alert you about new matches ({s.get_frequency_display().lower()}).'
    return {'success': True, 'saved_search_id': str(s.pk), 'message': text, 'whatsapp_summary': text}


@guarded
def remember_preferences(criteria: str = '') -> dict:
    """
    Remember the user's current search as their preferences for later conversations. Call it only when the user asks you to remember.

    Args:
        criteria: Leave empty; the current search is used
    """
    from apps.preferences import services as S
    from apps.properties import criteria as C
    user, data = _ctx_user.get(), json.loads(criteria)
    try:
        with transaction.atomic():           # no consent left behind when nothing was stored
            S.set_memory(user, True)
            S.state(user, data.pop('market'), data)
    except (C.CriteriaInvalid, C.LocationAmbiguous, S.PreferenceError):
        return {'success': False, 'error': 'These preferences could not be saved.'}
    text = '✅ I will remember these preferences. You can change or delete them in your profile.'
    return {'success': True, 'message': text, 'whatsapp_summary': text}
