"""
StripeIdentityProvider — passport and state-ID verification for US market.

Uses existing STRIPE_SECRET_KEY; no new credentials needed when Stripe is already configured.
Docs: https://stripe.com/docs/identity
"""
import logging

from django.conf import settings

from .base import VerificationProvider, VerificationResult, VerificationSession

logger = logging.getLogger(__name__)


class StripeIdentityProvider(VerificationProvider):
    name      = 'stripe_identity'
    supported = True

    def is_configured(self) -> bool:
        return bool(getattr(settings, 'STRIPE_SECRET_KEY', ''))

    def create_session(
        self,
        user_id:      str,
        doc_type:     str = 'document',
        redirect_url: str = '',
    ) -> VerificationSession:
        """
        Create a Stripe Identity VerificationSession.
        Returns session_url for the hosted document upload flow.
        """
        if not self.is_configured():
            raise ValueError('STRIPE_SECRET_KEY must be set')

        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')

        frontend = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        session  = stripe.identity.VerificationSession.create(
            type='document',
            metadata={'user_id': user_id},
            options={
                'document': {
                    'allowed_types': ['driving_license', 'id_card', 'passport'],
                    'require_id_number':      False,
                    'require_live_capture':   True,
                    'require_matching_selfie': True,
                },
            },
            return_url=redirect_url or frontend,
        )

        return VerificationSession(
            session_id  = session.id,
            session_url = session.url,
            provider    = self.name,
        )

    def verify_webhook_signature(self, payload_bytes: bytes, signature: str) -> bool:
        """Verify Stripe Identity webhook signature (separate secret from deal-lock webhook)."""
        import stripe
        stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
        secret = getattr(settings, 'STRIPE_IDENTITY_WEBHOOK_SECRET', '')
        if not secret:
            logger.error('STRIPE_IDENTITY_WEBHOOK_SECRET not set — rejecting webhook')
            return False
        try:
            stripe.Webhook.construct_event(payload_bytes, signature, secret)
            return True
        except stripe.error.SignatureVerificationError:
            return False
        except Exception as exc:
            logger.error('Stripe Identity webhook verification error: %s', exc)
            return False

    def parse_webhook(self, payload: dict) -> tuple[str, VerificationResult]:
        """Parse a Stripe Identity webhook event."""
        obj        = payload.get('data', {}).get('object', {})
        session_id = obj.get('id', '')
        vs_status  = (obj.get('status') or '').lower()

        if vs_status == 'verified':
            final_status = 'approved'
            confidence   = 0.98
        elif vs_status in ('requires_input', 'canceled'):
            final_status = 'declined'
            confidence   = 0.0
        else:
            final_status = 'pending'
            confidence   = 0.5

        verified_outputs = obj.get('verified_outputs') or {}
        extracted = {}
        if verified_outputs:
            extracted = {
                'first_name':      verified_outputs.get('first_name', ''),
                'last_name':       verified_outputs.get('last_name', ''),
                'document_number': verified_outputs.get('id_number', ''),
                'date_of_birth':   verified_outputs.get('dob', ''),
            }

        last_error = obj.get('last_error') or {}
        red_flags  = [last_error['code']] if last_error.get('code') else []

        return session_id, VerificationResult(
            status           = final_status,
            confidence       = confidence,
            extracted_fields = extracted,
            red_flags        = red_flags,
        )
