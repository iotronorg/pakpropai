import logging

import stripe
from django.conf import settings

logger = logging.getLogger(__name__)


class BillingUnavailableError(Exception):
    pass

# Plan slug → Stripe Price ID env key
_PLAN_PRICE_KEY = {
    'basic':        'STRIPE_PRICE_BASIC',
    'professional': 'STRIPE_PRICE_PROFESSIONAL',
    'enterprise':   'STRIPE_PRICE_ENTERPRISE',
}


def _api_key() -> str:
    return getattr(settings, 'STRIPE_SECRET_KEY', '')


def _price_id(plan: str) -> str:
    env_key = _PLAN_PRICE_KEY.get(plan, '')
    return getattr(settings, env_key, '') if env_key else ''


class StripeService:

    @classmethod
    def get_or_create_customer(cls, org) -> str:
        """Return existing Stripe customer ID or create a new one."""
        from apps.billing.models import OrgSubscription
        sub, _ = OrgSubscription.objects.get_or_create(organization=org)
        if sub.stripe_customer_id:
            return sub.stripe_customer_id

        stripe.api_key = _api_key()
        customer = stripe.Customer.create(
            name=org.name,
            email=getattr(org, 'email', '') or '',
            metadata={'org_id': str(org.id), 'org_slug': org.slug},
        )
        sub.stripe_customer_id = customer.id
        sub.save(update_fields=['stripe_customer_id', 'updated_at'])
        return customer.id

    @classmethod
    def create_checkout_session(cls, org, plan: str, success_url: str, cancel_url: str) -> str:
        """Create a Stripe Checkout Session and return the redirect URL."""
        price_id = _price_id(plan)
        if not price_id:
            raise ValueError(f"No Stripe price configured for plan '{plan}'")

        stripe.api_key = _api_key()
        customer_id = cls.get_or_create_customer(org)

        from apps.core.circuit_breaker import stripe_circuit
        session_params = dict(
            customer=customer_id,
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={'org_id': str(org.id), 'plan': plan},
            subscription_data={'metadata': {'org_id': str(org.id), 'plan': plan}},
            allow_promotion_codes=True,
        )
        session = stripe_circuit.call(
            stripe.checkout.Session.create,
            **session_params,
            fallback=None,
        )
        if session is None:
            raise BillingUnavailableError('Stripe is temporarily unavailable. Please try again shortly.')
        return session.url

    @classmethod
    def construct_webhook_event(cls, payload: bytes, sig_header: str):
        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.warning('STRIPE_WEBHOOK_SECRET not set — skipping signature verification (dev)')
            import json
            return type('Event', (), {'data': type('D', (), {'object': json.loads(payload)})(), 'type': json.loads(payload).get('type', '')})()
        return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)

    @classmethod
    def handle_checkout_completed(cls, session: dict) -> None:
        org_id   = session.get('metadata', {}).get('org_id')
        plan     = session.get('metadata', {}).get('plan', 'basic')
        sub_id   = session.get('subscription', '')
        customer = session.get('customer', '')
        if not org_id:
            return
        cls._activate_plan(org_id, plan, sub_id, customer, 'active')

    @classmethod
    def handle_subscription_updated(cls, subscription: dict) -> None:
        org_id = subscription.get('metadata', {}).get('org_id')
        plan   = subscription.get('metadata', {}).get('plan', 'basic')
        status = subscription.get('status', 'active')
        sub_id = subscription.get('id', '')
        customer = subscription.get('customer', '')
        period_end = subscription.get('current_period_end')
        cancel_at  = subscription.get('cancel_at_period_end', False)
        if not org_id:
            return
        cls._activate_plan(org_id, plan, sub_id, customer, status,
                           period_end=period_end, cancel_at_period_end=cancel_at)

    @classmethod
    def handle_subscription_deleted(cls, subscription: dict) -> None:
        org_id = subscription.get('metadata', {}).get('org_id')
        sub_id = subscription.get('id', '')
        if not org_id:
            return
        cls._downgrade_to_trial(org_id, sub_id)

    @classmethod
    def handle_payment_failed(cls, invoice: dict) -> None:
        customer_id = invoice.get('customer', '')
        if not customer_id:
            return
        try:
            from apps.billing.models import OrgSubscription
            sub = OrgSubscription.objects.select_related('organization__admin_user').get(
                stripe_customer_id=customer_id
            )
            sub.status = OrgSubscription.Status.PAST_DUE
            sub.save(update_fields=['status', 'updated_at'])
            # Notify org admin via WhatsApp
            admin = sub.organization.admin_user
            if admin and getattr(admin, 'phone', ''):
                from apps.notifications.utils import notify_user
                notify_user(
                    admin,
                    title='Payment failed',
                    body=(
                        f"⚠️ Your RealTron AI subscription payment failed. "
                        f"Please update your payment method to keep your {sub.plan.title()} plan active."
                    ),
                    event='billing_payment_failed',
                )
        except OrgSubscription.DoesNotExist:
            pass

    # ── Internal helpers ───────────────────────────────────────────────────────

    @classmethod
    def _activate_plan(cls, org_id: str, plan: str, sub_id: str, customer_id: str,
                       status: str, period_end=None, cancel_at_period_end: bool = False) -> None:
        from apps.billing.models import OrgSubscription
        from apps.organizations.models import Organization
        from datetime import datetime, timezone

        try:
            org = Organization.objects.get(id=org_id)
        except Organization.DoesNotExist:
            logger.error('StripeService._activate_plan: org %s not found', org_id)
            return

        sub, _ = OrgSubscription.objects.get_or_create(organization=org)
        sub.stripe_subscription_id = sub_id
        sub.stripe_customer_id     = customer_id or sub.stripe_customer_id
        sub.plan                   = plan
        sub.status                 = status
        sub.cancel_at_period_end   = cancel_at_period_end
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
        sub.save()

        was_trial = org.plan == 'trial'

        # Sync plan onto the Organization itself
        org.plan = plan
        org.save(update_fields=['plan', 'updated_at'])
        logger.info('StripeService: org %s upgraded to %s (%s)', org_id, plan, status)

        # Queue provisioning when moving out of trial
        if was_trial and plan != 'trial' and org.operational_mode == 'sandbox':
            from apps.whatsapp.tasks import provision_organization_live
            provision_organization_live.delay(str(org.id))
            logger.info('StripeService: queued provisioning for org %s', org_id)

    @classmethod
    def _downgrade_to_trial(cls, org_id: str, sub_id: str) -> None:
        from apps.billing.models import OrgSubscription
        from apps.organizations.models import Organization

        try:
            org = Organization.objects.get(id=org_id)
        except Organization.DoesNotExist:
            return

        OrgSubscription.objects.filter(organization=org).update(
            plan='trial', status=OrgSubscription.Status.CANCELLED,
            stripe_subscription_id='',
        )
        org.plan = 'trial'
        org.save(update_fields=['plan', 'updated_at'])
        logger.info('StripeService: org %s downgraded to trial', org_id)
