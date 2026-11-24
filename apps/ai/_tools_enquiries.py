"""The assistant's create_enquiry tool (T-2050). Runs only after the user confirmed (T-2012)."""
import logging

from ._tools_context import _ctx_conversation, _ctx_org, _ctx_user
from .confirmation import guarded

logger = logging.getLogger(__name__)


@guarded
def create_enquiry(listing: str, message: str) -> dict:
    """
    Send the user's enquiry about one listing to its lister. Call it when the user wants to contact the lister or agent of a specific listing, or asks to visit it.

    Args:
        listing: Listing id, reference number (RT-...), or its position in the last results, e.g. '2'
        message: What the user wants to say or ask, in the user's own words
    """
    from apps.enquiries import services as S
    from apps.leads.models import Lead
    user, conversation = _ctx_user.get(), _ctx_conversation.get()
    found = S.visible_listing(listing, _ctx_org.get())
    if user is None or found is None:
        return {'success': False, 'error': 'This listing is no longer available.'}
    whatsapp = conversation is None or conversation.channel == 'whatsapp'
    try:
        e, created = S.create(user, message=message, listing=found, via='assistant',
                              preferred_channel='whatsapp' if whatsapp else 'in_app',
                              source=Lead.Source.WHATSAPP if whatsapp else Lead.Source.WEB)
    except S.EnquiryError as exc:
        return {'success': False, 'error': str(exc)}
    text = (f'✅ Your enquiry was sent to {S.lister_name(found)}. Their reply will appear in your RealTron messages.'
            if created else f'✅ Your message was added to your open enquiry with {S.lister_name(found)}.')
    return {'success': True, 'enquiry_id': str(e.pk), 'message': text, 'whatsapp_summary': text}
