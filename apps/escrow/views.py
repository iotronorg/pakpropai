import logging
import secrets
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from .models import EscrowDeal
from .serializers import EscrowDealSerializer, InitiateDealLockSerializer, ConfirmPaymentSerializer

logger = logging.getLogger(__name__)

_PAYMENT_INSTRUCTIONS = {
    'jazzcash':  "Send PKR {amount:,} to JazzCash *03001234567*. Use your WhatsApp number as reference.",
    'easypaisa': "Send PKR {amount:,} to EasyPaisa *03001234567*. Use your WhatsApp number as reference.",
    'bank':      "Transfer PKR {amount:,} to Account *1234567890* (HBL — PakProp AI). Reference: your WhatsApp number.",
    'manual':    "Our team will contact you with payment details within 1 hour.",
    'safepay':   "A Safepay payment link will be sent to you shortly.",
}


class IsDashboardUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('admin', 'agent', 'developer')


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class DealLockInitiateView(APIView):
    """POST /deals/lock/ — buyer requests a 48h deal lock on a property."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ser = InitiateDealLockSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)

        prop    = ser.context['property']
        amount  = ser.validated_data['token_amount']
        gateway = ser.validated_data['payment_gateway']

        seller_token = secrets.token_hex(4).upper()  # 8-char hex token
        deal = EscrowDeal.objects.create(
            property                  = prop,
            buyer                     = request.user,
            token_amount              = amount,
            payment_gateway           = gateway,
            initiated_via             = EscrowDeal.Channel.DASHBOARD,
            status                    = EscrowDeal.Status.INITIATED,
            seller_confirmation_token = seller_token,
        )

        _notify_seller_lock_initiated(deal, seller_token)

        instructions = _PAYMENT_INSTRUCTIONS.get(gateway, _PAYMENT_INSTRUCTIONS['manual'])
        payment_message = instructions.format(amount=amount)

        return Response({
            'id':              str(deal.id),
            'status':          deal.status,
            'token_amount':    amount,
            'payment_gateway': gateway,
            'payment_message': payment_message,
            'message': (
                f"Deal lock requested for *{prop.title}*.\n\n"
                f"{payment_message}\n\n"
                "Once your payment is confirmed by our team, a 48-hour exclusivity window will begin."
            ),
        }, status=status.HTTP_201_CREATED)


class DealLockConfirmView(APIView):
    """PATCH /deals/lock/<id>/confirm/ — admin confirms payment and starts 48h window."""
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        if deal.status != EscrowDeal.Status.INITIATED:
            return Response(
                {'detail': f"Cannot confirm a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = ConfirmPaymentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        deal.payment_ref = ser.validated_data['payment_ref']
        if ser.validated_data.get('payment_gateway'):
            deal.payment_gateway = ser.validated_data['payment_gateway']
        if ser.validated_data.get('admin_notes'):
            deal.admin_notes = ser.validated_data['admin_notes']
        deal.save(update_fields=['payment_ref', 'payment_gateway', 'admin_notes', 'updated_at'])

        deal.activate_lock()
        _notify_buyer_lock_active(deal)

        return Response(EscrowDealSerializer(deal).data)


class DealLockCancelView(APIView):
    """PATCH /deals/lock/<id>/cancel/ — buyer or admin cancels a lock."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        is_admin = request.user.role == 'admin'
        is_buyer = deal.buyer_id == request.user.pk

        if not (is_admin or is_buyer):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if deal.status not in (EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED):
            return Response(
                {'detail': f"Cannot cancel a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deal.status = EscrowDeal.Status.CANCELLED
        deal.admin_notes = request.data.get('reason', deal.admin_notes)
        deal.save(update_fields=['status', 'admin_notes', 'updated_at'])

        return Response({'detail': 'Deal lock cancelled.', 'id': str(deal.id)})


class DealLockListView(generics.ListAPIView):
    """GET /deals/ — admin/agent/developer list of all deal locks."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [IsDashboardUser]

    def get_queryset(self):
        qs = EscrowDeal.objects.select_related('property', 'buyer', 'seller', 'agent')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


class MyDealLocksView(generics.ListAPIView):
    """GET /deals/mine/ — buyer's own deal locks."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return EscrowDeal.objects.filter(buyer=self.request.user).select_related(
            'property', 'buyer', 'seller', 'agent'
        )


class DealLockDetailView(generics.RetrieveAPIView):
    """GET /deals/lock/<id>/ — single deal lock detail."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role in ('admin', 'agent', 'developer'):
            return EscrowDeal.objects.select_related('property', 'buyer', 'seller', 'agent')
        return EscrowDeal.objects.filter(buyer=user).select_related('property', 'buyer', 'seller', 'agent')


class DealLockSellerConfirmView(APIView):
    """POST /deals/lock/<id>/seller-confirm/ — seller confirms awareness of deal lock."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)

        # Allow: the seller themselves, the assigned agent, or admin
        is_admin  = request.user.role == 'admin'
        is_seller = deal.property.owner_id == request.user.pk
        is_agent  = (deal.agent and hasattr(request.user, 'agent_profile')
                     and deal.agent == request.user.agent_profile)

        if not (is_admin or is_seller or is_agent):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if deal.seller_confirmed:
            return Response({'detail': 'Seller has already confirmed.'})

        if deal.status not in (EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED):
            return Response(
                {'detail': f"Cannot confirm a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token = request.data.get('token', '').strip().upper()
        if token and not is_admin:
            if token != deal.seller_confirmation_token:
                return Response({'detail': 'Invalid confirmation token.'}, status=status.HTTP_400_BAD_REQUEST)

        deal.seller_confirmed = True
        deal.save(update_fields=['seller_confirmed', 'updated_at'])
        return Response({'detail': 'Seller confirmation recorded.', 'id': str(deal.id)})


class DealLockReleaseView(APIView):
    """PATCH /deals/lock/<id>/release/ — admin marks deal successfully completed."""
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        if deal.status != EscrowDeal.Status.LOCKED:
            return Response(
                {'detail': f"Cannot release a deal in '{deal.status}' status. Only locked deals can be released."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deal.status = EscrowDeal.Status.RELEASED
        if request.data.get('admin_notes'):
            deal.admin_notes = request.data['admin_notes']
        deal.save(update_fields=['status', 'admin_notes', 'updated_at'])
        return Response({'detail': 'Deal marked as released.', 'id': str(deal.id), 'status': deal.status})


class DealLockDisputeView(APIView):
    """PATCH /deals/lock/<id>/dispute/ — admin flags a deal as disputed."""
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        if deal.status not in (EscrowDeal.Status.LOCKED, EscrowDeal.Status.INITIATED):
            return Response(
                {'detail': f"Cannot dispute a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deal.status = EscrowDeal.Status.DISPUTED
        if request.data.get('admin_notes'):
            deal.admin_notes = request.data['admin_notes']
        deal.save(update_fields=['status', 'admin_notes', 'updated_at'])
        return Response({'detail': 'Deal flagged as disputed.', 'id': str(deal.id), 'status': deal.status})


def _notify_buyer_lock_active(deal: EscrowDeal):
    try:
        hrs = deal.hours_remaining()
        msg = (
            f"✅ *Deal Lock Confirmed!*\n\n"
            f"🏠 *Property:* {deal.property.title}\n"
            f"💰 *Token Amount:* PKR {deal.token_amount:,}\n"
            f"⏳ *Exclusivity:* {hrs:.0f} hours remaining\n"
            f"📅 *Expires:* {deal.lock_expires_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
            "This property is now exclusively reserved for you. "
            "Contact your agent to proceed with the full transaction."
        )
        from apps.notifications.services import notify_user
        notify_user(deal.buyer, title="Deal Lock Confirmed", message=msg, event_type='deal_updates')
    except Exception as exc:
        logger.warning(f"Deal lock notify failed: {exc}")


def _notify_seller_lock_initiated(deal: EscrowDeal, token: str):
    """Notify the property owner that a buyer has initiated a deal lock."""
    try:
        seller_user = deal.property.owner
        if not seller_user or not seller_user.phone:
            return
        from apps.whatsapp.client import WhatsAppClient
        phone = seller_user.phone.lstrip('+')
        msg = (
            f"🔒 *Deal Lock Request*\n\n"
            f"A buyer has requested a 48-hour deal lock on your property:\n"
            f"🏠 *{deal.property.title}*\n"
            f"💰 *Token Amount:* PKR {deal.token_amount:,}\n\n"
            f"Your confirmation code: *{token}*\n\n"
            "Please contact your agent or reply with your code to confirm. "
            "If you did not authorize this, contact support immediately."
        )
        WhatsAppClient.send_text(phone, msg)
    except Exception as exc:
        logger.warning(f"Seller deal lock notify failed: {exc}")
