import logging
from datetime import datetime

from django.db import IntegrityError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ledger import UsageLedger
from .limits import PLAN_LIMITS

logger = logging.getLogger(__name__)

_UPGRADEABLE_PLANS = {'basic', 'professional', 'enterprise'}


def _safe_origin(request) -> str | None:
    """
    Return a validated origin for use in redirect URLs.
    Prevents open-redirect attacks via a crafted Origin header.
    Returns None when the origin is not in ALLOWED_FRONTEND_ORIGINS (reject with 400).
    When ALLOWED_FRONTEND_ORIGINS is not configured, always returns FRONTEND_URL.
    """
    from django.conf import settings as _s
    frontend = getattr(_s, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
    allowed  = getattr(_s, 'ALLOWED_FRONTEND_ORIGINS', [])
    origin   = request.headers.get('Origin', frontend)
    if not allowed:
        return frontend
    return origin if origin in allowed else None


class BillingUsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role not in ('developer', 'admin'):
            return Response({'detail': 'Forbidden.'}, status=403)

        if user.role == 'admin':
            return Response({'plan': 'enterprise', 'usage': {}})

        try:
            org = user.owned_organization
        except Exception:
            return Response({'detail': 'No organization linked to this account.'}, status=404)

        plan = getattr(org, 'plan', 'trial')
        org_id = str(org.id)
        plan_cfg = PLAN_LIMITS.get(plan, PLAN_LIMITS['trial'])
        period = datetime.utcnow().strftime('%Y-%m')

        agents_used    = UsageLedger.get_agent_count(org_id)
        inventory_used = UsageLedger.get_inventory_count(org_id)
        wa_tokens_used = UsageLedger.get_wa_token_count(org_id)

        def _entry(used: int, limit_key: str, extra: dict | None = None) -> dict:
            limit = plan_cfg.get(limit_key)
            entry = {'used': used, 'limit': limit}
            if extra:
                entry.update(extra)
            return entry

        return Response({
            'plan': plan,
            'usage': {
                'agents':    _entry(agents_used,    'max_agents'),
                'inventory': _entry(inventory_used, 'max_inventory'),
                'wa_tokens': _entry(wa_tokens_used, 'monthly_wa_tokens', {'period': period}),
            },
        })


class BillingCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.role != 'developer':
            return Response({'detail': 'Only organization admins can manage billing.'}, status=403)

        plan = request.data.get('plan', '')
        if plan not in _UPGRADEABLE_PLANS:
            return Response({'detail': f"Invalid plan '{plan}'."}, status=400)

        try:
            org = user.owned_organization
        except Exception:
            return Response({'detail': 'No organization linked to this account.'}, status=404)

        origin = _safe_origin(request)
        if origin is None:
            return Response({'detail': 'Origin not allowed.'}, status=400)
        success_url = f"{origin}/organization/billing/success?plan={plan}"
        cancel_url  = f"{origin}/organization/settings"

        try:
            from .gateway import BillingGatewayDispatcher
            checkout_url = BillingGatewayDispatcher.create_checkout(org, plan, success_url, cancel_url)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
        except Exception as exc:
            logger.error('Billing checkout creation failed: %s', exc)
            return Response({'detail': 'Payment provider error. Please try again.'}, status=502)

        if checkout_url is None:
            # manual gateway — admin upgrades plan offline
            return Response({'checkout_url': None, 'manual': True})

        return Response({'checkout_url': checkout_url})


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(APIView):
    authentication_classes = []
    permission_classes      = []

    def post(self, request):
        payload    = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

        try:
            from .stripe_service import StripeService
            event = StripeService.construct_webhook_event(payload, sig_header)
        except Exception as exc:
            logger.warning('Stripe webhook signature verification failed: %s', exc)
            return Response({'detail': 'Invalid signature.'}, status=400)

        # Idempotency guard: skip already-processed events
        from .models import WebhookEvent
        try:
            WebhookEvent.objects.create(gateway='stripe', event_id=event.id)
        except IntegrityError:
            logger.info('Stripe webhook: duplicate event %s — skipping', event.id)
            return Response({'received': True})

        event_type = event.type
        obj        = event.data.object

        try:
            from .stripe_service import StripeService
            if event_type == 'checkout.session.completed':
                StripeService.handle_checkout_completed(dict(obj))
            elif event_type in ('customer.subscription.updated', 'customer.subscription.created'):
                StripeService.handle_subscription_updated(dict(obj))
            elif event_type == 'customer.subscription.deleted':
                StripeService.handle_subscription_deleted(dict(obj))
            elif event_type == 'invoice.payment_failed':
                StripeService.handle_payment_failed(dict(obj))
            else:
                logger.debug('Stripe webhook: unhandled event type %s', event_type)
        except Exception as exc:
            logger.error('Stripe webhook handler error event=%s: %s', event_type, exc)
            return Response({'detail': 'Handler error.'}, status=500)

        return Response({'received': True})


class OrgPaymentSettingsView(APIView):
    """
    GET  /billing/payment-settings/ — return org's deal-lock gateway config
    PATCH /billing/payment-settings/ — update it (sensitive fields write-only)
    """
    permission_classes = [IsAuthenticated]

    _SENSITIVE = {'safepay_secret_key', 'bsecure_client_secret'}
    _FIELDS = [
        'gateway',
        'safepay_merchant_key', 'safepay_secret_key', 'safepay_environment',
        'bsecure_client_id', 'bsecure_client_secret', 'bsecure_environment',
        'jazzcash_number', 'easypaisa_number',
        'bank_account_number', 'bank_account_name',
    ]

    def _get_org(self, request):
        if request.user.role != 'developer':
            return None, Response({'detail': 'Forbidden.'}, status=403)
        try:
            return request.user.owned_organization, None
        except Exception:
            return None, Response({'detail': 'No organization linked to this account.'}, status=404)

    def get(self, request):
        from apps.organizations.models import OrgPaymentSettings
        org, err = self._get_org(request)
        if err:
            return err

        ps, _ = OrgPaymentSettings.objects.get_or_create(organization=org)
        data = {}
        for f in self._FIELDS:
            val = getattr(ps, f, '')
            if f in self._SENSITIVE:
                data[f] = '__configured__' if val else ''
            else:
                data[f] = val
        return Response(data)

    def patch(self, request):
        from apps.organizations.models import OrgPaymentSettings
        org, err = self._get_org(request)
        if err:
            return err

        ps, _ = OrgPaymentSettings.objects.get_or_create(organization=org)
        allowed = set(self._FIELDS)
        update_fields = []
        for key, val in request.data.items():
            if key not in allowed:
                continue
            # Don't clear sensitive fields if sent blank (means "keep existing")
            if key in self._SENSITIVE and val == '':
                continue
            setattr(ps, key, val)
            update_fields.append(key)

        if update_fields:
            update_fields.append('updated_at')
            ps.save(update_fields=update_fields)

        return Response({'saved': True})


@method_decorator(csrf_exempt, name='dispatch')
class SafepayBillingWebhookView(APIView):
    """Handles Safepay payment.success events for SaaS plan activation."""
    authentication_classes = []
    permission_classes      = []

    def post(self, request):
        import hmac, hashlib
        from apps.config.services import SystemConfigService

        secret_key = SystemConfigService.get('safepay_secret_key', default='')
        payload    = request.body

        # Verify Safepay HMAC signature (X-SFPY-SIGNATURE header) — always required
        sig = request.META.get('HTTP_X_SFPY_SIGNATURE', '')
        if not secret_key:
            logger.warning('Safepay billing webhook: safepay_secret_key not configured; rejecting')
            return Response({'detail': 'Webhook not configured.'}, status=400)
        if not sig:
            logger.warning('Safepay billing webhook: missing X-SFPY-SIGNATURE header')
            return Response({'detail': 'Missing signature.'}, status=400)
        expected = hmac.new(secret_key.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            logger.warning('Safepay billing webhook: invalid signature')
            return Response({'detail': 'Invalid signature.'}, status=400)

        try:
            data     = request.data
            event    = data.get('event', '')
            order_id = (data.get('data') or {}).get('tracker', {}).get('merchant_order_id') or data.get('order_id', '')
        except Exception as exc:
            logger.warning('Safepay billing webhook: bad payload: %s', exc)
            return Response({'detail': 'Bad payload.'}, status=400)

        if event == 'payment.success' and order_id and order_id.startswith('billing-'):
            from .models import WebhookEvent
            try:
                WebhookEvent.objects.create(gateway='safepay', event_id=order_id)
            except IntegrityError:
                logger.info('Safepay billing webhook: duplicate order %s — skipping', order_id)
                return Response({'received': True})
            from .gateway import activate_from_order
            activate_from_order(order_id)

        return Response({'received': True})


@method_decorator(csrf_exempt, name='dispatch')
class BSecureBillingWebhookView(APIView):
    """Handles bSecure order.completed events for SaaS plan activation."""
    authentication_classes = []
    permission_classes      = []

    def post(self, request):
        import hmac, hashlib
        from apps.config.services import SystemConfigService

        secret_key = SystemConfigService.get('bsecure_client_secret', default='')
        payload    = request.body
        sig        = request.META.get('HTTP_X_BSECURE_SIGNATURE', '')

        if not secret_key:
            logger.warning('bSecure billing webhook: bsecure_client_secret not configured; rejecting')
            return Response({'detail': 'Webhook not configured.'}, status=400)
        if not sig:
            logger.warning('bSecure billing webhook: missing X-bSecure-Signature header')
            return Response({'detail': 'Missing signature.'}, status=400)
        expected = hmac.new(secret_key.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            logger.warning('bSecure billing webhook: invalid signature')
            return Response({'detail': 'Invalid signature.'}, status=400)

        try:
            data     = request.data
            status   = data.get('status', '')
            order_id = data.get('order_ref') or data.get('order_id', '')
        except Exception as exc:
            logger.warning('bSecure billing webhook: bad payload: %s', exc)
            return Response({'detail': 'Bad payload.'}, status=400)

        if status in ('completed', 'COMPLETED') and order_id and order_id.startswith('billing-'):
            from .models import WebhookEvent
            try:
                WebhookEvent.objects.create(gateway='bsecure', event_id=order_id)
            except IntegrityError:
                logger.info('bSecure billing webhook: duplicate order %s — skipping', order_id)
                return Response({'received': True})
            from .gateway import activate_from_order
            activate_from_order(order_id)

        return Response({'received': True})


class BillingPortalView(APIView):
    """POST /billing/portal/ — redirect to Stripe Customer Portal for subscription management."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.conf import settings as _settings
        import stripe as _stripe
        from .models import OrgSubscription
        from .gateway import _cfg

        try:
            org = request.user.owned_organization
        except Exception:
            return Response({'detail': 'No organization linked to this account.'}, status=400)

        secret_key = _cfg('stripe_secret_key') or getattr(_settings, 'STRIPE_SECRET_KEY', '')
        if not secret_key:
            return Response({'detail': 'Stripe is not configured.'}, status=400)

        _stripe.api_key = secret_key

        try:
            sub = OrgSubscription.objects.get(organization=org)
        except OrgSubscription.DoesNotExist:
            return Response({'detail': 'No subscription found.'}, status=400)

        if not sub.stripe_customer_id:
            return Response({'detail': 'No Stripe customer found.'}, status=400)

        origin = _safe_origin(request)
        if origin is None:
            return Response({'detail': 'Origin not allowed.'}, status=400)
        session = _stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f"{origin}/organization/settings/",
        )
        return Response({'url': session.url})


class BillingInvoiceView(APIView):
    """GET /billing/invoices/ — list past Stripe invoices for the org."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.conf import settings as _settings
        import stripe as _stripe
        from .models import OrgSubscription
        from .gateway import _cfg

        try:
            org = request.user.owned_organization
        except Exception:
            return Response({'invoices': []})

        secret_key = _cfg('stripe_secret_key') or getattr(_settings, 'STRIPE_SECRET_KEY', '')
        if not secret_key:
            return Response({'invoices': []})

        _stripe.api_key = secret_key

        try:
            sub = OrgSubscription.objects.get(organization=org)
        except OrgSubscription.DoesNotExist:
            return Response({'invoices': []})

        if not sub.stripe_customer_id:
            return Response({'invoices': []})

        invoices = _stripe.Invoice.list(customer=sub.stripe_customer_id, limit=20)
        return Response({
            'invoices': [
                {
                    'id':                 inv.id,
                    'number':             inv.number,
                    'amount_due':         inv.amount_due,
                    'amount_paid':        inv.amount_paid,
                    'currency':           inv.currency.upper(),
                    'status':             inv.status,
                    'created':            inv.created,
                    'hosted_invoice_url': inv.hosted_invoice_url,
                    'invoice_pdf':        inv.invoice_pdf,
                }
                for inv in invoices.data
            ]
        })
