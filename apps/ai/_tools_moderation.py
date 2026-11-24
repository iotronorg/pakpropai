"""submit_listing (T-2014): the user's own draft goes to review, only after confirmation."""
import logging

from ._tools_context import _ctx_user
from .confirmation import guarded

logger = logging.getLogger(__name__)


def own_draft(user, listing: str = ''):
    """A draft of this private person by id or reference, else the latest one."""
    import uuid

    from apps.properties.models import Property
    qs = Property.objects.filter(owner=user, status=Property.Status.DRAFT,
                                 listing_owner_type=Property.ListingOwnerType.CLIENT)
    ref = (listing or '').strip()
    if not ref:
        return qs.order_by('-created_at').first()
    try:
        return qs.filter(pk=uuid.UUID(ref)).first()
    except ValueError:
        return qs.filter(ref_no__iexact=ref).first()


@guarded
def submit_listing(listing: str = '') -> dict:
    """
    Send the user's draft listing to our team for review, after which it becomes public.

    Args:
        listing: Listing id or reference number (RT-...). Empty = the user's latest draft
    """
    from apps.properties.lifecycle import TransitionError, transition
    from apps.properties.models import ListingTransition, Property
    try:
        user = _ctx_user.get()
        p = own_draft(user, listing)
        if p is None:
            return {'success': False, 'error': 'This draft is no longer available.'}
        transition(p, Property.Status.PENDING_REVIEW, actor=user, reason='Submitted through the assistant',
                   source=ListingTransition.Source.API)
        text = f'✅ {p.ref_no} was sent for review. We will tell you when it is public.'
        return {'success': True, 'listing_id': str(p.pk), 'message': text, 'whatsapp_summary': text}
    except TransitionError as exc:
        missing = ', '.join(exc.fields) if exc.fields else ''
        return {'success': False, 'error': f'{exc} {missing}'.strip()}
