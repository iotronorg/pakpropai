import json
import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.escrow.models import EscrowDeal
from .models import Payment
from .services import PaymentService

logger = logging.getLogger(__name__)

SUPPORTED_ONLINE_GATEWAYS = {'safepay', 'bsecure'}


def _safe_origin(request) -> str | None:
    """
    Return a validated origin for use in redirect URLs.
    Prevents open-redirect attacks via a crafted Origin header.
    Returns None when the origin is not in ALLOWED_FRONTEND_ORIGINS (reject with 400).
    When ALLOWED_FRONTEND_ORIGINS is not configured, always returns FRONTEND_URL.
    """
    frontend = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
    allowed  = getattr(settings, 'ALLOWED_FRONTEND_ORIGINS', [])
    origin   = request.headers.get('Origin', frontend)
    if not allowed:
        return frontend
    return origin if origin in allowed else None


class CreateCheckoutView(APIView):
    """
    POST /payments/checkout/<deal_id>/
    Creates a gateway checkout session and returns the redirect URL.
    Gateway is read from deal.payment_gateway or from query param ?gateway=safepay.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, deal_id):
        deal = get_object_or_404(EscrowDeal, id=deal_id)

        if deal.buyer != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if deal.status != EscrowDeal.Status.INITIATED:
            return Response(
                {'detail': f"Cannot initiate payment for a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.config.services import SystemConfigService
        active_gw = SystemConfigService.get_active_gateway()
        if active_gw == 'manual':
            return Response(
                {'detail': 'Online payment is disabled. Admin has set the gateway to manual only.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gateway = request.data.get('gateway') or request.query_params.get('gateway', active_gw)
        if gateway not in SUPPORTED_ONLINE_GATEWAYS or gateway != active_gw:
            return Response(
                {'detail': f"Only the '{active_gw}' gateway is currently enabled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        origin = _safe_origin(request)
        if origin is None:
            return Response({'detail': 'Origin not allowed.'}, status=status.HTTP_400_BAD_REQUEST)
        redirect_url = f"{origin}/payments/return/?status=success&deal_id={deal.id}"
        cancel_url   = f"{origin}/payments/return/?status=cancelled&deal_id={deal.id}"

        try:
            result = PaymentService.create_checkout(
                deal=deal,
                gateway=gateway,
                redirect_url=redirect_url,
                cancel_url=cancel_url,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Checkout creation failed deal={deal.id} gateway={gateway}: {exc}")
            return Response(
                {'detail': 'Payment gateway error. Please try manual payment.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'deal_id':      str(deal.id),
            'gateway':      gateway,
            'checkout_url': result['checkout_url'],
            'message':      f'Redirecting to {gateway.title()} payment page...',
        })


class PaymentReturnView(APIView):
    """
    GET /payments/return/?status=success&deal_id=<uuid>
    Called by the authenticated frontend after gateway redirect.
    Real confirmation comes via webhook — this just shows status.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ret_status = request.query_params.get('status', 'unknown')
        deal_id    = request.query_params.get('deal_id', '')

        if not deal_id:
            return Response({'detail': 'Missing deal_id.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            deal = EscrowDeal.objects.get(id=deal_id)
        except EscrowDeal.DoesNotExist:
            return Response({'detail': 'Deal not found.'}, status=status.HTTP_404_NOT_FOUND)

        if deal.buyer != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'deal_id':        str(deal.id),
            'deal_status':    deal.status,
            'return_status':  ret_status,
            'property':       deal.property.title,
            'message': (
                "Payment received! Your deal lock will be activated within a few minutes."
                if ret_status == 'success'
                else "Payment was not completed. You can try again or choose manual payment."
            ),
        })


# ── Webhook endpoints (no auth — verified by HMAC signature) ─────────────────

@method_decorator(csrf_exempt, name='dispatch')
class SafepayWebhookView(APIView):
    """POST /payments/webhook/safepay/ — Safepay payment notification."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        raw   = request.body
        sig   = request.headers.get('X-Safepay-Signature', '')
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return Response({'detail': 'Bad JSON'}, status=status.HTTP_400_BAD_REQUEST)

        activated = PaymentService.handle_webhook('safepay', payload, raw, sig)
        return Response({'received': True, 'activated': activated})


@method_decorator(csrf_exempt, name='dispatch')
class bSecureWebhookView(APIView):
    """POST /payments/webhook/bsecure/ — bSecure payment notification."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        raw = request.body
        sig = request.headers.get('X-bSecure-Signature', '')
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return Response({'detail': 'Bad JSON'}, status=status.HTTP_400_BAD_REQUEST)

        activated = PaymentService.handle_webhook('bsecure', payload, raw, sig)
        return Response({'received': True, 'activated': activated})


class PaymentListView(APIView):
    """GET /payments/ — admin-only list of all payments."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        payments = Payment.objects.select_related('user', 'escrow_deal').order_by('-created_at')[:200]
        data = [
            {
                'id':           str(p.id),
                'user':         p.user.phone,
                'amount':       p.amount,
                'currency':     p.currency,
                'purpose':      p.purpose,
                'gateway':      p.gateway,
                'status':       p.status,
                'reference':    p.reference,
                'checkout_url': p.checkout_url,
                'deal_id':      str(p.escrow_deal_id) if p.escrow_deal_id else None,
                'created_at':   p.created_at.isoformat(),
            }
            for p in payments
        ]
        return Response({'count': len(data), 'results': data})
