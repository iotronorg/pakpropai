"""
Payment gateway clients for RealTron AI.

Safepay  — primary gateway (Pakistan-native, card + JazzCash + EasyPaisa)
bSecure  — secondary gateway (wider wallet support)
Stripe   — global gateway (AE, GB, US and all other Stripe-enabled markets)

System NEVER holds funds: payments go directly to merchant account at gateway.
"""
import hashlib
import hmac
import json
import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ─── Safepay ──────────────────────────────────────────────────────────────────

class SafepayGateway:
    """
    Safepay v1 API.
    Docs: https://getsafepay.com/docs
    Sandbox base: https://sandbox.api.getsafepay.com
    Production base: https://api.getsafepay.com
    """

    @classmethod
    def _base(cls, environment: str = None) -> str:
        env = environment or getattr(settings, 'SAFEPAY_ENVIRONMENT', 'sandbox')
        return (
            'https://api.getsafepay.com'
            if env == 'production'
            else 'https://sandbox.api.getsafepay.com'
        )

    @classmethod
    def _checkout_base(cls, environment: str = None) -> str:
        env = environment or getattr(settings, 'SAFEPAY_ENVIRONMENT', 'sandbox')
        return (
            'https://getsafepay.com'
            if env == 'production'
            else 'https://sandbox.getsafepay.com'
        )

    @classmethod
    def create_checkout(
        cls,
        order_id: str,
        amount: int,
        redirect_url: str,
        cancel_url: str,
        customer_phone: str = '',
        description: str = 'Deal Lock Token',
        merchant_key: str = None,
        secret_key: str = None,
        environment: str = None,
        currency: str = 'PKR',
    ) -> dict:
        """
        Create a Safepay checkout session.
        Returns: {'checkout_token': str, 'checkout_url': str}
        """
        from apps.config.services import SystemConfigService
        mk  = merchant_key or SystemConfigService.get('safepay_merchant_key') or getattr(settings, 'SAFEPAY_MERCHANT_KEY', '')
        sk  = secret_key   or SystemConfigService.get('safepay_secret_key')   or getattr(settings, 'SAFEPAY_SECRET_KEY', '')
        env = environment  or SystemConfigService.get('safepay_environment')   or getattr(settings, 'SAFEPAY_ENVIRONMENT', 'sandbox')

        if not mk or not sk:
            raise ValueError("SAFEPAY_MERCHANT_KEY and SAFEPAY_SECRET_KEY must be set.")

        payload = {
            'merchant':    mk,
            'intent':      'CYBERSOURCE',
            'mode':        'payment',
            'currency':    currency,
            'amount':      amount,
            'order_id':    str(order_id),
            'cancel_url':  cancel_url,
            'redirect_url': redirect_url,
            'description': description,
        }

        from apps.core.circuit_breaker import safepay_circuit
        resp = safepay_circuit.call(
            requests.post,
            f'{cls._base(env)}/v1/payments/create',
            json=payload,
            headers={
                'Content-Type':           'application/json',
                'X-SFPY-MERCHANT-SECRET': sk,
            },
            timeout=10,
            fallback=None,
        )
        if resp is None:
            return {'status': 'gateway_unavailable', 'checkout_url': None}
        resp.raise_for_status()
        data = resp.json()

        token = data.get('data', {}).get('token') or data.get('token', '')
        checkout_url = f"{cls._checkout_base(env)}/checkout?token={token}"

        return {'checkout_token': token, 'checkout_url': checkout_url}

    @classmethod
    def verify_webhook(cls, payload_bytes: bytes, signature: str) -> bool:
        """Verify Safepay webhook HMAC-SHA256 signature."""
        secret = getattr(settings, 'SAFEPAY_SECRET_KEY', '')
        if not secret:
            logger.error("SAFEPAY_SECRET_KEY not set — rejecting unsigned webhook")
            return False
        if not signature:
            logger.warning("Safepay webhook: missing signature header")
            return False
        expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @classmethod
    def parse_webhook(cls, payload: dict) -> Optional[dict]:
        """
        Extract payment result from webhook payload.
        Returns dict with: order_id, status ('paid'|'failed'), tracker, amount
        """
        data   = payload.get('data', {})
        status = data.get('status', '').lower()
        return {
            'order_id': data.get('order_id') or data.get('tracker', ''),
            'tracker':  data.get('tracker', ''),
            'status':   'paid' if status in ('paid', 'successful', 'captured') else 'failed',
            'amount':   data.get('net') or data.get('amount', 0),
        }


# ─── bSecure ──────────────────────────────────────────────────────────────────

class bSecureGateway:
    """
    bSecure v1 API.
    Docs: https://bsecure.pk/docs
    """

    _TOKEN_CACHE: dict = {}

    @classmethod
    def _base(cls, environment: str = None) -> str:
        env = environment or getattr(settings, 'BSECURE_ENVIRONMENT', 'sandbox')
        return (
            'https://api.bsecure.pk'
            if env == 'production'
            else 'https://sandbox.api.bsecure.pk'
        )

    @classmethod
    def _get_access_token(cls, client_id: str = None, client_secret: str = None, environment: str = None) -> str:
        from apps.config.services import SystemConfigService
        cid = client_id     or SystemConfigService.get('bsecure_client_id')     or getattr(settings, 'BSECURE_CLIENT_ID', '')
        cs  = client_secret or SystemConfigService.get('bsecure_client_secret') or getattr(settings, 'BSECURE_CLIENT_SECRET', '')

        if not cid or not cs:
            raise ValueError("BSECURE_CLIENT_ID and BSECURE_CLIENT_SECRET must be set.")

        resp = requests.post(
            f'{cls._base(environment)}/v1/oauth/token',
            json={
                'client_id':     cid,
                'client_secret': cs,
                'grant_type':    'client_credentials',
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('access_token', '')

    @classmethod
    def create_checkout(
        cls,
        order_id: str,
        amount: int,
        redirect_url: str,
        cancel_url: str,
        customer_phone: str = '',
        description: str = 'Deal Lock Token',
        client_id: str = None,
        client_secret: str = None,
        environment: str = None,
        currency: str = 'PKR',
    ) -> dict:
        from apps.config.services import SystemConfigService
        env   = environment or SystemConfigService.get('bsecure_environment') or getattr(settings, 'BSECURE_ENVIRONMENT', 'sandbox')
        token = cls._get_access_token(client_id=client_id, client_secret=client_secret, environment=env)
        payload = {
            'order_id':           str(order_id),
            'amount':             amount,
            'currency':           currency,
            'order_type':         'normal',
            'success_redirect_url': redirect_url,
            'failure_redirect_url': cancel_url,
            'products': [{
                'name':  description,
                'sku':   str(order_id),
                'price': amount,
                'qty':   1,
            }],
        }
        if customer_phone:
            payload['customer'] = {
                'country_code': '+92',
                'phone_number': customer_phone.lstrip('+92').lstrip('92'),
            }

        from apps.core.circuit_breaker import bsecure_circuit
        resp = bsecure_circuit.call(
            requests.post,
            f'{cls._base(env)}/v1/order/create',
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=10,
            fallback=None,
        )
        if resp is None:
            return {'status': 'gateway_unavailable', 'checkout_url': None}
        resp.raise_for_status()
        data = resp.json()

        checkout_url = data.get('redirect_url') or data.get('data', {}).get('redirect_url', '')
        return {'checkout_token': str(order_id), 'checkout_url': checkout_url}

    @classmethod
    def verify_webhook(cls, payload_bytes: bytes, signature: str) -> bool:
        secret = getattr(settings, 'BSECURE_CLIENT_SECRET', '')
        if not secret:
            logger.error("BSECURE_CLIENT_SECRET not set — rejecting unsigned webhook")
            return False
        if not signature:
            logger.warning("bSecure webhook: missing signature header")
            return False
        expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @classmethod
    def parse_webhook(cls, payload: dict) -> Optional[dict]:
        status = payload.get('status', '').lower()
        order  = payload.get('order', {})
        return {
            'order_id': order.get('order_ref') or payload.get('order_ref', ''),
            'tracker':  payload.get('transaction_ref', ''),
            'status':   'paid' if status in ('paid', 'completed', 'success') else 'failed',
            'amount':   order.get('amount', 0),
        }


# ─── Stripe ───────────────────────────────────────────────────────────────────

# ISO 4217 currencies with no subunit (amount sent as-is to Stripe)
_ZERO_DECIMAL_CURRENCIES = {'BIF', 'CLP', 'GNF', 'JPY', 'KMF', 'KRW', 'MGA', 'PYG', 'RWF', 'UGX', 'VND', 'VUV', 'XAF', 'XOF', 'XPF'}


def _to_stripe_amount(amount: int, currency: str) -> int:
    """Convert a human-scale amount (e.g. 500 AED) to Stripe minor units (50000 fils)."""
    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
        return amount
    return amount * 100


class StripePaymentGateway:
    """
    Stripe Checkout Sessions — global card payments for deal locks.
    Covers AE (AED), GB (GBP), US (USD), and all other Stripe-enabled markets.
    Amounts passed in via token_amount are human-scale (e.g. 500 AED);
    this class converts to Stripe minor units internally.
    """

    @classmethod
    def create_checkout(
        cls,
        order_id: str,
        amount: int,
        redirect_url: str,
        cancel_url: str,
        customer_phone: str = '',
        description: str = 'Deal Lock Token',
        currency: str = 'usd',
        **kwargs,
    ) -> dict:
        """
        Create a Stripe Checkout Session.
        Returns: {'checkout_token': session_id, 'checkout_url': session_url}
        """
        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
        if not stripe.api_key:
            raise ValueError("STRIPE_SECRET_KEY must be set.")

        stripe_amount = _to_stripe_amount(amount, currency)

        from apps.core.circuit_breaker import stripe_circuit

        def _create():
            return stripe.checkout.Session.create(
                payment_method_types=['card'],
                mode='payment',
                client_reference_id=str(order_id),
                success_url=redirect_url,
                cancel_url=cancel_url,
                line_items=[{
                    'price_data': {
                        'currency': currency.lower(),
                        'product_data': {'name': description},
                        'unit_amount': stripe_amount,
                    },
                    'quantity': 1,
                }],
                payment_intent_data={
                    'metadata': {'order_id': str(order_id)},
                },
            )

        session = stripe_circuit.call(_create, fallback=None)
        if session is None:
            return {'status': 'gateway_unavailable', 'checkout_url': None}

        return {'checkout_token': session.id, 'checkout_url': session.url}

    @classmethod
    def verify_webhook(cls, payload_bytes: bytes, signature: str) -> bool:
        """Verify Stripe webhook signature via stripe.Webhook.construct_event."""
        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
        secret = getattr(settings, 'STRIPE_DEAL_LOCK_WEBHOOK_SECRET', '')
        if not secret:
            logger.error("STRIPE_DEAL_LOCK_WEBHOOK_SECRET not set — rejecting webhook")
            return False
        try:
            stripe.Webhook.construct_event(payload_bytes, signature, secret)
            return True
        except stripe.error.SignatureVerificationError:
            return False
        except Exception as exc:
            logger.error("Stripe webhook verification error: %s", exc)
            return False

    @classmethod
    def create_sepa_payment(
        cls,
        order_id:     str,
        amount:       int,
        currency:     str,
        iban:         str,
        account_name: str,
    ) -> dict:
        """
        Create a Stripe SEPA Direct Debit PaymentIntent for EU deal locks.
        Currency must be 'eur'. Returns {payment_intent_id, client_secret, status}.
        Deal transitions INITIATED → LOCKED via payment_intent.succeeded webhook.
        """
        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
        if not stripe.api_key:
            raise ValueError('STRIPE_SECRET_KEY must be set')

        pm = stripe.PaymentMethod.create(
            type         = 'sepa_debit',
            sepa_debit   = {'iban': iban},
            billing_details = {'name': account_name},
        )

        pi = stripe.PaymentIntent.create(
            amount               = _to_stripe_amount(amount, currency),
            currency             = currency.lower(),
            payment_method_types = ['sepa_debit'],
            payment_method       = pm.id,
            confirm              = True,
            mandate_data         = {'customer_acceptance': {'type': 'offline'}},
            metadata             = {'order_id': str(order_id)},
        )

        return {
            'payment_intent_id': pi.id,
            'client_secret':     pi.client_secret,
            'status':            pi.status,
        }

    @classmethod
    def parse_webhook(cls, payload: dict) -> Optional[dict]:
        """
        Extract payment result from a Stripe webhook event dict.
        Handles checkout.session.completed and payment_intent.succeeded.
        Returns: {order_id, status ('paid'|'failed'), tracker, amount}
        """
        event_type = payload.get('type', '')
        obj = payload.get('data', {}).get('object', {})

        if event_type == 'checkout.session.completed':
            return {
                'order_id': obj.get('client_reference_id', ''),
                'tracker':  obj.get('payment_intent', ''),
                'status':   'paid' if obj.get('payment_status') == 'paid' else 'failed',
                'amount':   obj.get('amount_total', 0),
            }

        if event_type == 'payment_intent.succeeded':
            return {
                'order_id': obj.get('metadata', {}).get('order_id', ''),
                'tracker':  obj.get('id', ''),
                'status':   'paid',
                'amount':   obj.get('amount', 0),
            }

        return {'order_id': '', 'tracker': '', 'status': 'failed', 'amount': 0}


# ─── Gateway factory ──────────────────────────────────────────────────────────

class PaymentService:
    """Single entry point: create a checkout session for any supported gateway."""

    GATEWAYS = {
        'safepay':  SafepayGateway,
        'bsecure':  bSecureGateway,
        'stripe':   StripePaymentGateway,
    }

    @classmethod
    def create_checkout(
        cls,
        deal,          # EscrowDeal instance
        gateway: str,  # 'safepay' | 'bsecure'
        redirect_url: str,
        cancel_url: str,
        org=None,
    ) -> dict:
        """
        Create a gateway checkout session for a deal lock.
        Saves a Payment record and returns checkout_url.
        """
        from .models import Payment

        gw_class = cls.GATEWAYS.get(gateway)
        if not gw_class:
            raise ValueError(f"Unsupported gateway: {gateway}")

        creds = {}
        if org is not None:
            try:
                ps = org.payment_settings
                if gateway == 'safepay' and ps.safepay_merchant_key:
                    creds = {
                        'merchant_key': ps.safepay_merchant_key,
                        'secret_key':   ps.safepay_secret_key,
                        'environment':  ps.safepay_environment,
                    }
                elif gateway == 'bsecure' and ps.bsecure_client_id:
                    creds = {
                        'client_id':     ps.bsecure_client_id,
                        'client_secret': ps.bsecure_client_secret,
                        'environment':   ps.bsecure_environment,
                    }
            except Exception:
                pass

        result = gw_class.create_checkout(
            order_id       = str(deal.id),
            amount         = deal.token_amount,
            currency       = deal.currency,
            redirect_url   = redirect_url,
            cancel_url     = cancel_url,
            customer_phone = deal.buyer.phone,
            description    = f'Deal Lock — {deal.property.title}',
            **creds,
        )

        Payment.objects.update_or_create(
            escrow_deal = deal,
            gateway     = gateway,
            defaults={
                'user':           deal.buyer,
                'amount':         deal.token_amount,
                'currency':       deal.currency,
                'purpose':        Payment.Purpose.ESCROW_TOKEN,
                'status':         Payment.Status.PENDING,
                'checkout_token': result['checkout_token'],
                'checkout_url':   result['checkout_url'],
            },
        )

        return result

    @classmethod
    def handle_webhook(cls, gateway: str, payload: dict, raw_bytes: bytes, signature: str) -> bool:
        """
        Process an inbound webhook from a payment gateway.
        Returns True if a deal was successfully activated.
        """
        from .models import Payment
        from apps.escrow.models import EscrowDeal
        from apps.escrow.views import _notify_buyer_lock_active

        gw_class = cls.GATEWAYS.get(gateway)
        if not gw_class:
            logger.error(f"Webhook from unknown gateway: {gateway}")
            return False

        if not gw_class.verify_webhook(raw_bytes, signature):
            logger.warning(f"{gateway} webhook signature mismatch")
            return False

        parsed = gw_class.parse_webhook(payload)
        logger.info(f"{gateway} webhook: order_id={parsed['order_id']} status={parsed['status']}")

        if parsed['status'] != 'paid':
            # Mark payment failed if we have a record
            Payment.objects.filter(
                checkout_token=parsed.get('tracker', ''),
                gateway=gateway,
            ).update(status=Payment.Status.FAILED)
            return False

        # Find deal by order_id
        try:
            deal = EscrowDeal.objects.get(id=parsed['order_id'])
        except (EscrowDeal.DoesNotExist, Exception) as exc:
            logger.error(f"Webhook: deal not found for order_id={parsed['order_id']}: {exc}")
            return False

        if deal.status != EscrowDeal.Status.INITIATED:
            logger.info(f"Webhook: deal {deal.id} already in status={deal.status}, skipping")
            return False

        # Update payment record
        Payment.objects.filter(escrow_deal=deal, gateway=gateway).update(
            status=Payment.Status.COMPLETED,
            reference=parsed.get('tracker', ''),
            webhook_payload=payload,
        )

        # Activate the 48h lock
        deal.payment_gateway = gateway
        deal.payment_ref     = parsed.get('tracker', '')
        deal.save(update_fields=['payment_gateway', 'payment_ref', 'updated_at'])
        deal.activate_lock()
        _notify_buyer_lock_active(deal)

        logger.info(f"Deal lock ACTIVATED via {gateway} webhook: deal={deal.id} property={deal.property.title}")
        return True
