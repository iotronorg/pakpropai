import logging
from datetime import datetime

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ledger import UsageLedger
from .limits import PLAN_LIMITS

logger = logging.getLogger(__name__)

_UPGRADEABLE_PLANS = {'basic', 'professional', 'enterprise'}


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

        origin = request.headers.get('Origin', 'http://localhost:3000')
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

        # Verify Safepay HMAC signature (X-SFPY-SIGNATURE header)
        sig = request.META.get('HTTP_X_SFPY_SIGNATURE', '')
        if secret_key and sig:
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
            from .gateway import activate_from_order
            activate_from_order(order_id)

        return Response({'received': True})


@method_decorator(csrf_exempt, name='dispatch')
class BSecureBillingWebhookView(APIView):
    """Handles bSecure order.completed events for SaaS plan activation."""
    authentication_classes = []
    permission_classes      = []

    def post(self, request):
        try:
            data     = request.data
            status   = data.get('status', '')
            order_id = data.get('order_ref') or data.get('order_id', '')
        except Exception as exc:
            logger.warning('bSecure billing webhook: bad payload: %s', exc)
            return Response({'detail': 'Bad payload.'}, status=400)

        if status in ('completed', 'COMPLETED') and order_id and order_id.startswith('billing-'):
            from .gateway import activate_from_order
            activate_from_order(order_id)

        return Response({'received': True})
