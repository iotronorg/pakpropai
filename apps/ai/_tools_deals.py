import logging
from ._tools_context import _ctx_user, _ctx_phone, _ctx_org

logger = logging.getLogger(__name__)


def _resolve_currency(org) -> str:
    """Return the ISO 4217 currency code for the org's market. Never raises."""
    try:
        from apps.markets.registry import get_market_config
        country = getattr(org, 'country', 'PK') or 'PK'
        return get_market_config(country).currency
    except Exception:
        return 'PKR'


def _get_org_payment_info(org):
    """
    Return (jazzcash_no, easypaisa_no, bank_no, bank_name) preferring
    org-level OrgPaymentSettings over platform SystemConfig defaults.
    """
    from apps.config.services import SystemConfigService
    jazz = easypaisa = bank_no = bank_name = ''
    try:
        ps = org.payment_settings
        jazz      = ps.jazzcash_number or ''
        easypaisa = ps.easypaisa_number or ''
        bank_no   = ps.bank_account_number or ''
        bank_name = ps.bank_account_name or ''
    except Exception:
        pass
    # Fall back to platform-level defaults for any empty field
    if not jazz:      jazz      = SystemConfigService.get('jazzcash_number', '')
    if not easypaisa: easypaisa = SystemConfigService.get('easypaisa_number', '')
    if not bank_no:   bank_no   = SystemConfigService.get('bank_account_number', '')
    if not bank_name: bank_name = SystemConfigService.get('bank_account_name', '')
    return jazz, easypaisa, bank_no, bank_name


def initiate_deal_lock(
    property_id: str,
    token_amount: int,
    payment_method: str = '',
) -> dict:
    """
    Lock a property exclusively for the buyer for 48 hours by paying a token amount.
    Use this when a user says they want to 'lock', 'reserve', 'book token', or 'secure' a property.

    Args:
        property_id: The UUID of the property to lock (from search results).
        token_amount: Token/deposit amount in the organisation's local currency.
        payment_method: Payment method. For Pakistan: 'jazzcash', 'easypaisa', 'bank', 'manual'.
                        For other markets: 'bank', 'manual', 'safepay', 'bsecure'.
                        Leave blank to auto-select from org's configured gateway.
    """
    user  = _ctx_user.get()
    phone = _ctx_phone.get()
    org   = _ctx_org.get()

    if not user or not phone:
        return {'success': False, 'message': 'Could not identify your account. Please try again.'}

    # ── Resolve market currency ────────────────────────────────────────────────
    currency = _resolve_currency(org)
    country  = getattr(org, 'country', 'PK') or 'PK'
    is_pk    = country.upper() == 'PK'

    # ── Amount bounds (configurable; 0 = no bound) ────────────────────────────
    from apps.config.services import SystemConfigService
    try:
        min_amount = int(SystemConfigService.get('deal_lock_min_amount', '0')) or 0
        max_amount = int(SystemConfigService.get('deal_lock_max_amount', '0')) or 0
    except (ValueError, TypeError):
        min_amount = max_amount = 0

    if min_amount > 0 and token_amount < min_amount:
        return {
            'success': False,
            'message': (
                f"Token amount must be at least *{currency} {min_amount:,}*.\n"
                "Please specify a higher amount."
            ),
        }
    if max_amount > 0 and token_amount > max_amount:
        return {
            'success': False,
            'message': (
                f"Token amount cannot exceed *{currency} {max_amount:,}*.\n"
                "Please specify a lower amount."
            ),
        }

    # ── Valid payment methods — PK markets add JazzCash / EasyPaisa ───────────
    _pk_only_methods    = {'jazzcash', 'easypaisa'}
    _universal_methods  = {'bank', 'manual', 'safepay', 'bsecure'}
    valid_methods = (_pk_only_methods | _universal_methods) if is_pk else _universal_methods

    # Auto-select default when caller omits payment_method
    if not payment_method:
        payment_method = 'jazzcash' if is_pk else 'manual'

    if payment_method not in valid_methods:
        payment_method = 'jazzcash' if is_pk else 'manual'

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
            token_amount    = token_amount,
            currency        = currency,
            payment_gateway = payment_method,
            initiated_via   = EscrowDeal.Channel.WHATSAPP,
            status          = EscrowDeal.Status.INITIATED,
        )

        # ── Online checkout link (Safepay / bSecure) ──────────────────────────
        online_link = ''
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
                logger.warning("Could not create %s checkout for deal %s: %s", active_gw, deal.id, exc)

        # ── Manual payment instructions ───────────────────────────────────────
        jazz, easypaisa, bank_no, bank_name = _get_org_payment_info(org) if org else ('', '', '', '', )

        if not online_link:
            if payment_method == 'jazzcash' and not jazz:
                return {'success': False, 'message': 'JazzCash payments are not configured. Please contact support or choose a different payment method.'}
            if payment_method == 'easypaisa' and not easypaisa:
                return {'success': False, 'message': 'EasyPaisa payments are not configured. Please contact support or choose a different payment method.'}
            if payment_method == 'bank' and not bank_no:
                return {'success': False, 'message': 'Bank transfer details are not configured. Please contact support or choose a different payment method.'}

        bank_label = f"{bank_no} ({bank_name})" if bank_name else bank_no
        amt_str    = f"{currency} {token_amount:,}"

        _PAYMENT_INSTRUCTIONS = {
            'jazzcash':  f"Send *{amt_str}* to JazzCash *{jazz}*. Use your WhatsApp number as reference.",
            'easypaisa': f"Send *{amt_str}* to EasyPaisa *{easypaisa}*. Use your WhatsApp number as reference.",
            'bank':      f"Transfer *{amt_str}* to Account *{bank_label}*. Reference: your WhatsApp number.",
            'safepay':   f"Pay *{amt_str}* online via the secure link below.",
            'bsecure':   f"Pay *{amt_str}* online via the secure link below.",
            'manual':    "Our team will contact you with payment details within 1 hour.",
        }
        payment_msg = _PAYMENT_INSTRUCTIONS.get(payment_method, _PAYMENT_INSTRUCTIONS['manual'])

        online_section = (f"\n💳 *Pay Online (instant):*\n{online_link}\n") if online_link else ''

        summary = (
            f"🔒 *Deal Lock Requested!*\n\n"
            f"🏠 *Property:* {prop.title}\n"
            f"📍 *Location:* {prop.city} — {prop.location}\n"
            f"💰 *Token Amount:* {amt_str}\n"
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
            'token_amount': token_amount,
            'currency':     currency,
            'whatsapp_summary': summary,
            '_instruction': 'Return the whatsapp_summary VERBATIM.',
        }

    except Exception as exc:
        logger.error("initiate_deal_lock failed: %s", exc, exc_info=True)
        return {
            'success': False,
            'message': 'Could not process your deal lock request. Please try again.',
        }
