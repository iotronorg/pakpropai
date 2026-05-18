import logging
from ._tools_context import _ctx_user, _ctx_phone

logger = logging.getLogger(__name__)


def initiate_deal_lock(
    property_id: str,
    token_amount_pkr: int,
    payment_method: str = 'jazzcash',
) -> dict:
    """
    Lock a property exclusively for the buyer for 48 hours by paying a token amount.
    Use this when a user says they want to 'lock', 'reserve', 'book token', or 'secure' a property.

    Args:
        property_id: The UUID of the property to lock (from search results).
        token_amount_pkr: Token amount in PKR. Must be between 25,000 and 100,000.
        payment_method: Payment method — one of 'jazzcash', 'easypaisa', 'bank', 'manual'.
    """
    user  = _ctx_user.get()
    phone = _ctx_phone.get()

    if not user or not phone:
        return {'success': False, 'message': 'Could not identify your account. Please try again.'}

    if token_amount_pkr < 25_000 or token_amount_pkr > 100_000:
        return {
            'success': False,
            'message': (
                "Token amount must be between *PKR 25,000* and *PKR 100,000*.\n"
                "Please specify an amount in this range."
            ),
        }

    valid_methods = {'jazzcash', 'easypaisa', 'bank', 'manual'}
    if payment_method not in valid_methods:
        payment_method = 'jazzcash'

    try:
        from apps.properties.models import Property
        from apps.escrow.models import EscrowDeal

        try:
            prop = Property.objects.get(id=property_id, is_active=True)
        except Property.DoesNotExist:
            return {'success': False, 'message': 'Property not found. Please search again and use the exact property ID.'}

        existing = EscrowDeal.objects.filter(
            property=prop,
            status__in=[EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED]
        ).first()
        if existing:
            if existing.buyer == user:
                return {
                    'success': False,
                    'message': (
                        f"You already have an active deal lock on *{prop.title}*.\n"
                        f"Status: *{existing.get_status_display()}*"
                    ),
                }
            return {
                'success': False,
                'message': (
                    f"⚠️ *{prop.title}* is currently locked by another buyer.\n"
                    "Please check back after the lock expires (within 48 hours)."
                ),
            }

        deal = EscrowDeal.objects.create(
            property        = prop,
            buyer           = user,
            token_amount    = token_amount_pkr,
            payment_gateway = payment_method,
            initiated_via   = EscrowDeal.Channel.WHATSAPP,
            status          = EscrowDeal.Status.INITIATED,
        )

        online_link = ''
        from apps.config.services import SystemConfigService
        active_gw = SystemConfigService.get_active_gateway()
        if active_gw in ('safepay', 'bsecure'):
            try:
                from apps.payments.services import PaymentService
                base_url = SystemConfigService.get('base_url')
                result = PaymentService.create_checkout(
                    deal=deal,
                    gateway=active_gw,
                    redirect_url=f"{base_url}/payments/return/?status=success&deal_id={deal.id}",
                    cancel_url=f"{base_url}/payments/return/?status=cancelled&deal_id={deal.id}",
                )
                online_link = result.get('checkout_url', '')
            except Exception as exc:
                logger.warning(f"Could not create {active_gw} checkout for deal {deal.id}: {exc}")

        jazzcash_number   = SystemConfigService.get('jazzcash_number')
        easypaisa_number  = SystemConfigService.get('easypaisa_number')
        bank_account_no   = SystemConfigService.get('bank_account_number')
        bank_account_name = SystemConfigService.get('bank_account_name')

        if not online_link:
            if payment_method == 'jazzcash' and not jazzcash_number:
                return {'success': False, 'message': 'JazzCash payments are not configured yet. Please contact support or choose a different payment method.'}
            if payment_method == 'easypaisa' and not easypaisa_number:
                return {'success': False, 'message': 'EasyPaisa payments are not configured yet. Please contact support or choose a different payment method.'}
            if payment_method == 'bank' and not bank_account_no:
                return {'success': False, 'message': 'Bank transfer payments are not configured yet. Please contact support or choose a different payment method.'}

        bank_label = f"{bank_account_no} ({bank_account_name})" if bank_account_name else bank_account_no
        _PAYMENT_INSTRUCTIONS = {
            'jazzcash':  f"Send *PKR {token_amount_pkr:,}* to JazzCash *{jazzcash_number}*. Use your WhatsApp number as reference.",
            'easypaisa': f"Send *PKR {token_amount_pkr:,}* to EasyPaisa *{easypaisa_number}*. Use your WhatsApp number as reference.",
            'bank':      f"Transfer *PKR {token_amount_pkr:,}* to Account *{bank_label}*. Reference: your WhatsApp number.",
            'manual':    "Our team will contact you with payment details within 1 hour.",
        }
        payment_msg = _PAYMENT_INSTRUCTIONS.get(payment_method, _PAYMENT_INSTRUCTIONS['manual'])

        online_section = (f"\n💳 *Pay Online (instant):*\n{online_link}\n") if online_link else ''

        summary = (
            f"🔒 *Deal Lock Requested!*\n\n"
            f"🏠 *Property:* {prop.title}\n"
            f"📍 *Location:* {prop.city} — {prop.location}\n"
            f"💰 *Token Amount:* PKR {token_amount_pkr:,}\n"
            f"🔑 *Lock ID:* `{str(deal.id)[:8].upper()}`\n\n"
            f"*Payment Instructions:*\n{payment_msg}"
            f"{online_section}\n\n"
            "✅ Once payment is confirmed, your *48-hour exclusivity* window begins automatically.\n"
            "You will receive a WhatsApp confirmation immediately."
        )

        return {
            'success':      True,
            'deal_id':      str(deal.id),
            'property':     prop.title,
            'token_amount': token_amount_pkr,
            'whatsapp_summary': summary,
            '_instruction': 'Return the whatsapp_summary VERBATIM.',
        }

    except Exception as exc:
        logger.error(f"initiate_deal_lock failed: {exc}", exc_info=True)
        return {
            'success': False,
            'message': 'Could not process your deal lock request. Please try again.',
        }
