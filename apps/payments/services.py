"""
Payment gateway clients for PakProp AI.

Safepay  — primary gateway (Pakistan-native, card + JazzCash + EasyPaisa)
bSecure  — secondary gateway (wider wallet support)

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
    def _base(cls) -> str:
        env = getattr(settings, 'SAFEPAY_ENVIRONMENT', 'sandbox')
        return (
            'https://api.getsafepay.com'
            if env == 'production'
            else 'https://sandbox.api.getsafepay.com'
        )

    @classmethod
    def _checkout_base(cls) -> str:
        env = getattr(settings, 'SAFEPAY_ENVIRONMENT', 'sandbox')
        return (
            'https://getsafepay.com'
            if env == 'production'
            else 'https://sandbox.getsafepay.com'
        )

    @classmethod
    def create_checkout(
        cls,
        order_id: str,
        amount_pkr: int,
        redirect_url: str,
        cancel_url: str,
        customer_phone: str = '',
        description: str = 'Deal Lock Token',
    ) -> dict:
        """
        Create a Safepay checkout session.
        Returns: {'checkout_token': str, 'checkout_url': str}
        """
        merchant_key = getattr(settings, 'SAFEPAY_MERCHANT_KEY', '')
        secret_key   = getattr(settings, 'SAFEPAY_SECRET_KEY', '')

        if not merchant_key or not secret_key:
            raise ValueError("SAFEPAY_MERCHANT_KEY and SAFEPAY_SECRET_KEY must be set.")

        payload = {
            'merchant':    merchant_key,
            'intent':      'CYBERSOURCE',
            'mode':        'payment',
            'currency':    'PKR',
            'amount':      amount_pkr,
            'order_id':    str(order_id),
            'cancel_url':  cancel_url,
            'redirect_url': redirect_url,
            'description': description,
        }

        resp = requests.post(
            f'{cls._base()}/v1/payments/create',
            json=payload,
            headers={
                'Content-Type':           'application/json',
                'X-SFPY-MERCHANT-SECRET': secret_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        token = data.get('data', {}).get('token') or data.get('token', '')
        checkout_url = f"{cls._checkout_base()}/checkout?token={token}"

        return {'checkout_token': token, 'checkout_url': checkout_url}

    @classmethod
    def verify_webhook(cls, payload_bytes: bytes, signature: str) -> bool:
        """Verify Safepay webhook HMAC-SHA256 signature."""
        secret = getattr(settings, 'SAFEPAY_SECRET_KEY', '').encode()
        expected = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or '')

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
    def _base(cls) -> str:
        env = getattr(settings, 'BSECURE_ENVIRONMENT', 'sandbox')
        return (
            'https://api.bsecure.pk'
            if env == 'production'
            else 'https://sandbox.api.bsecure.pk'
        )

    @classmethod
    def _get_access_token(cls) -> str:
        client_id     = getattr(settings, 'BSECURE_CLIENT_ID', '')
        client_secret = getattr(settings, 'BSECURE_CLIENT_SECRET', '')

        if not client_id or not client_secret:
            raise ValueError("BSECURE_CLIENT_ID and BSECURE_CLIENT_SECRET must be set.")

        resp = requests.post(
            f'{cls._base()}/v1/oauth/token',
            json={
                'client_id':     client_id,
                'client_secret': client_secret,
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
        amount_pkr: int,
        redirect_url: str,
        cancel_url: str,
        customer_phone: str = '',
        description: str = 'Deal Lock Token',
    ) -> dict:
        token = cls._get_access_token()
        payload = {
            'order_id':           str(order_id),
            'amount':             amount_pkr,
            'currency':           'PKR',
            'order_type':         'normal',
            'success_redirect_url': redirect_url,
            'failure_redirect_url': cancel_url,
            'products': [{
                'name':  description,
                'sku':   str(order_id),
                'price': amount_pkr,
                'qty':   1,
            }],
        }
        if customer_phone:
            payload['customer'] = {
                'country_code': '+92',
                'phone_number': customer_phone.lstrip('+92').lstrip('92'),
            }

        resp = requests.post(
            f'{cls._base()}/v1/order/create',
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        checkout_url = data.get('redirect_url') or data.get('data', {}).get('redirect_url', '')
        return {'checkout_token': str(order_id), 'checkout_url': checkout_url}

    @classmethod
    def verify_webhook(cls, payload_bytes: bytes, signature: str) -> bool:
        secret = getattr(settings, 'BSECURE_CLIENT_SECRET', '').encode()
        expected = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or '')

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


# ─── Gateway factory ──────────────────────────────────────────────────────────

class PaymentService:
    """Single entry point: create a checkout session for any supported gateway."""

    GATEWAYS = {
        'safepay':  SafepayGateway,
        'bsecure':  bSecureGateway,
    }

    @classmethod
    def create_checkout(
        cls,
        deal,          # EscrowDeal instance
        gateway: str,  # 'safepay' | 'bsecure'
        redirect_url: str,
        cancel_url: str,
    ) -> dict:
        """
        Create a gateway checkout session for a deal lock.
        Saves a Payment record and returns checkout_url.
        """
        from .models import Payment

        gw_class = cls.GATEWAYS.get(gateway)
        if not gw_class:
            raise ValueError(f"Unsupported gateway: {gateway}")

        result = gw_class.create_checkout(
            order_id       = str(deal.id),
            amount_pkr     = deal.token_amount,
            redirect_url   = redirect_url,
            cancel_url     = cancel_url,
            customer_phone = deal.buyer.phone,
            description    = f'Deal Lock — {deal.property.title}',
        )

        Payment.objects.update_or_create(
            escrow_deal = deal,
            gateway     = gateway,
            defaults={
                'user':           deal.buyer,
                'amount_pkr':     deal.token_amount,
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
