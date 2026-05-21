"""
BillingGatewayDispatcher — routes SaaS plan payments to the configured gateway.

billing_gateway (SystemConfig):
  stripe   → Stripe Checkout (global, recurring subscription)
  safepay  → Safepay one-time payment (Pakistan)
  bsecure  → bSecure one-time payment (Pakistan)
  manual   → returns None; admin upgrades plan manually via Django admin
"""
import logging
import uuid

logger = logging.getLogger(__name__)

# PKR plan prices (monthly) — configurable via SystemConfig
_PLAN_PRICE_PKR_KEY = {
    'basic':        'billing_price_basic_pkr',
    'professional': 'billing_price_professional_pkr',
    'enterprise':   'billing_price_enterprise_pkr',
}
_DEFAULT_PLAN_PKR = {
    'basic': 13_000, 'professional': 40_000, 'enterprise': 120_000,
}


def _cfg(key: str, default: str = '') -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get(key, default=default)


class BillingGatewayDispatcher:

    @classmethod
    def active_gateway(cls) -> str:
        return _cfg('billing_gateway', 'manual')

    @classmethod
    def create_checkout(cls, org, plan: str, success_url: str, cancel_url: str) -> str | None:
        """
        Create a checkout session for a plan upgrade.
        Returns redirect URL, or None for manual gateway (admin handles offline).
        Raises ValueError for missing configuration.
        """
        gateway = cls.active_gateway()

        if gateway == 'stripe':
            return cls._stripe_checkout(org, plan, success_url, cancel_url)
        if gateway == 'safepay':
            return cls._safepay_checkout(org, plan, success_url, cancel_url)
        if gateway == 'bsecure':
            return cls._bsecure_checkout(org, plan, success_url, cancel_url)

        # manual — admin upgrades plan via Django admin
        return None

    # ── Stripe ────────────────────────────────────────────────────────────────

    @classmethod
    def _stripe_checkout(cls, org, plan: str, success_url: str, cancel_url: str) -> str:
        import stripe as _stripe
        secret_key = _cfg('stripe_secret_key')
        if not secret_key:
            raise ValueError("Stripe secret key is not configured. Set it in Admin → Setup.")

        price_key = f'stripe_price_{plan}'
        price_id  = _cfg(price_key)
        if not price_id:
            raise ValueError(
                f"Stripe price ID for '{plan}' plan is not configured. "
                f"Set '{price_key}' in Admin → Setup."
            )

        _stripe.api_key = secret_key

        from apps.billing.models import OrgSubscription
        sub, _ = OrgSubscription.objects.get_or_create(organization=org)

        if not sub.stripe_customer_id:
            customer = _stripe.Customer.create(
                name=org.name,
                email=getattr(org, 'email', '') or '',
                metadata={'org_id': str(org.id)},
            )
            sub.stripe_customer_id = customer.id
            sub.save(update_fields=['stripe_customer_id', 'updated_at'])

        session = _stripe.checkout.Session.create(
            customer=sub.stripe_customer_id,
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={'org_id': str(org.id), 'plan': plan},
            subscription_data={'metadata': {'org_id': str(org.id), 'plan': plan}},
            allow_promotion_codes=True,
        )
        return session.url

    # ── Safepay ───────────────────────────────────────────────────────────────

    @classmethod
    def _safepay_checkout(cls, org, plan: str, success_url: str, cancel_url: str) -> str:
        import requests as _req
        from django.conf import settings as _settings

        merchant_key = _cfg('safepay_merchant_key') or getattr(_settings, 'SAFEPAY_MERCHANT_KEY', '')
        secret_key   = _cfg('safepay_secret_key')   or getattr(_settings, 'SAFEPAY_SECRET_KEY', '')
        if not merchant_key or not secret_key:
            raise ValueError("Safepay merchant key / secret key not configured. Set them in Admin → Setup.")

        env = _cfg('safepay_environment', 'sandbox')
        base = 'https://api.getsafepay.com' if env == 'production' else 'https://sandbox.api.getsafepay.com'
        checkout_base = 'https://getsafepay.com' if env == 'production' else 'https://sandbox.getsafepay.com'

        amount   = _plan_pkr(plan)
        currency = getattr(org, 'currency', 'PKR')
        order_id = f"billing-{org.id}-{plan}-{uuid.uuid4().hex[:8]}"

        payload = {
            'merchant': merchant_key, 'intent': 'CYBERSOURCE',
            'mode': 'payment', 'currency': currency, 'amount': amount,
            'order_id': order_id, 'cancel_url': cancel_url,
            'redirect_url': success_url,
            'description': f"RealTron AI — {plan.title()} Plan (monthly)",
        }
        resp = _req.post(
            f'{base}/v1/payments/create', json=payload,
            headers={'Content-Type': 'application/json', 'X-SFPY-MERCHANT-SECRET': secret_key},
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json().get('data', {}).get('token') or resp.json().get('token', '')

        # Store pending upgrade so webhook can activate plan
        _store_pending(org, plan, order_id, 'safepay')

        return f"{checkout_base}/checkout?token={token}"

    # ── bSecure ───────────────────────────────────────────────────────────────

    @classmethod
    def _bsecure_checkout(cls, org, plan: str, success_url: str, cancel_url: str) -> str:
        import requests as _req
        from django.conf import settings as _settings

        client_id     = _cfg('bsecure_client_id')     or getattr(_settings, 'BSECURE_CLIENT_ID', '')
        client_secret = _cfg('bsecure_client_secret') or getattr(_settings, 'BSECURE_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            raise ValueError("bSecure client ID / secret not configured. Set them in Admin → Setup.")

        env  = _cfg('bsecure_environment', 'sandbox')
        base = 'https://api.bsecure.pk' if env == 'production' else 'https://sandbox-api.bsecure.pk'

        # Obtain access token
        tok = _req.post(
            f'{base}/v1/oauth/token',
            json={'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'client_credentials'},
            timeout=10,
        )
        tok.raise_for_status()
        access_token = tok.json().get('access_token', '')

        amount   = _plan_pkr(plan)
        currency = getattr(org, 'currency', 'PKR')
        order_id = f"billing-{org.id}-{plan}-{uuid.uuid4().hex[:8]}"

        payload = {
            'order_id': order_id, 'amount': amount, 'currency': currency,
            'order_type': 'normal',
            'success_redirect_url': success_url,
            'failure_redirect_url': cancel_url,
            'products': [{'name': f"RealTron AI {plan.title()} Plan", 'sku': plan, 'price': amount, 'qty': 1}],
        }
        resp = _req.post(
            f'{base}/v1/order/create', json=payload,
            headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'},
            timeout=10,
        )
        resp.raise_for_status()
        checkout_url = resp.json().get('redirect_url') or resp.json().get('data', {}).get('redirect_url', '')

        _store_pending(org, plan, order_id, 'bsecure')

        return checkout_url


# ── Helpers ────────────────────────────────────────────────────────────────────

def _plan_pkr(plan: str) -> int:
    try:
        val = _cfg(_PLAN_PRICE_PKR_KEY.get(plan, ''))
        return int(val) if val else _DEFAULT_PLAN_PKR.get(plan, 10_000)
    except (ValueError, TypeError):
        return _DEFAULT_PLAN_PKR.get(plan, 10_000)


def _store_pending(org, plan: str, order_id: str, gateway: str) -> None:
    """Cache pending plan upgrade so the payment webhook can activate it."""
    from django.core.cache import cache
    cache.set(f'billing:pending:{order_id}', {'org_id': str(org.id), 'plan': plan, 'gateway': gateway}, 3600 * 24)


def activate_from_order(order_id: str) -> bool:
    """Called by Safepay/bSecure billing webhooks on successful payment."""
    from django.core.cache import cache
    from apps.billing.stripe_service import StripeService

    data = cache.get(f'billing:pending:{order_id}')
    if not data:
        logger.warning('activate_from_order: no pending data for order %s', order_id)
        return False

    StripeService._activate_plan(
        org_id=data['org_id'],
        plan=data['plan'],
        sub_id='',
        customer_id='',
        status='active',
    )
    cache.delete(f'billing:pending:{order_id}')
    logger.info('activate_from_order: org %s activated plan %s via %s', data['org_id'], data['plan'], data['gateway'])
    return True
