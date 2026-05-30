import logging
import secrets
from django.db import transaction, IntegrityError
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from apps.properties.models import Property
from apps.core.metrics import deal_locks_total
from apps.core.permissions import get_user_org
from .models import EscrowDeal
from .serializers import EscrowDealSerializer, InitiateDealLockSerializer, ConfirmPaymentSerializer

logger = logging.getLogger(__name__)

def _get_payment_instructions(gateway: str, amount: int, currency: str, org=None) -> str:
    from apps.config.services import SystemConfigService
    org_ps = None
    if org is not None:
        try:
            org_ps = org.payment_settings
        except Exception:
            pass
    if gateway == 'jazzcash':
        number = (org_ps.jazzcash_number if org_ps and org_ps.jazzcash_number else None) or SystemConfigService.get('jazzcash_number')
        if number:
            return f"Send {currency} {amount:,} to JazzCash *{number}*. Use your WhatsApp number as reference."
        return "Our team will contact you with JazzCash payment details within 1 hour."
    if gateway == 'easypaisa':
        number = (org_ps.easypaisa_number if org_ps and org_ps.easypaisa_number else None) or SystemConfigService.get('easypaisa_number')
        if number:
            return f"Send {currency} {amount:,} to EasyPaisa *{number}*. Use your WhatsApp number as reference."
        return "Our team will contact you with EasyPaisa payment details within 1 hour."
    if gateway == 'bank':
        account = (org_ps.bank_account_number if org_ps and org_ps.bank_account_number else None) or SystemConfigService.get('bank_account_number')
        name    = (org_ps.bank_account_name if org_ps and org_ps.bank_account_name else None) or SystemConfigService.get('bank_account_name') or 'RealTron AI'
        if account:
            return f"Transfer {currency} {amount:,} to Account *{account}* ({name}). Reference: your WhatsApp number."
        return "Our team will contact you with bank transfer details within 1 hour."
    if gateway == 'safepay':
        return "A Safepay payment link will be sent to you shortly."
    return "Our team will contact you with payment details within 1 hour."


def _check_high_risk_deal(prop, deal) -> None:
    """Log and alert admin when a deal lock is initiated on a high-risk property."""
    ai_score   = getattr(prop, 'ai_score', None)
    risk_level = getattr(prop, 'risk_level', None)
    is_high_risk = (ai_score is not None and ai_score < 40) or risk_level == 'HIGH'
    if not is_high_risk:
        return
    logger.warning(
        'HIGH-RISK deal lock initiated: deal=%s property=%s ai_score=%s risk_level=%s',
        deal.id, prop.id, ai_score, risk_level,
    )
    try:
        from apps.core.models import AuditLog
        AuditLog.objects.create(
            event='high_risk_deal_lock',
            target_type='EscrowDeal',
            target_id=str(deal.id),
            metadata={
                'property_id':  str(prop.id),
                'property_title': prop.title,
                'ai_score':     ai_score,
                'risk_level':   risk_level,
                'buyer_phone':  getattr(deal.buyer, 'phone', ''),
            },
        )
    except Exception as exc:
        logger.error('_check_high_risk_deal: failed to create audit log: %s', exc)


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

        property_id = ser.validated_data['property_id']
        amount      = ser.validated_data['token_amount']
        gateway     = ser.validated_data['payment_gateway']
        org         = None

        try:
            with transaction.atomic():
                prop = Property.objects.select_for_update(of=('self',)).select_related(
                    'organization'
                ).get(id=property_id, is_active=True)

                org = prop.organization
                if org is not None:
                    try:
                        ps = org.payment_settings
                        if ps.gateway != 'manual':
                            gateway = ps.gateway
                    except Exception:
                        pass

                if EscrowDeal.objects.filter(
                    property=prop,
                    status__in=[EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED],
                ).exists():
                    raise DRFValidationError(
                        'This property already has an active deal lock. '
                        'Try again after it expires.'
                    )
                seller_token = secrets.token_hex(4).upper()
                deal = EscrowDeal.objects.create(
                    property                  = prop,
                    buyer                     = request.user,
                    token_amount              = amount,
                    payment_gateway           = gateway,
                    initiated_via             = EscrowDeal.Channel.DASHBOARD,
                    status                    = EscrowDeal.Status.INITIATED,
                    seller_confirmation_token = seller_token,
                )
        except IntegrityError:
            raise DRFValidationError(
                'This property already has an active deal lock. '
                'Try again after it expires.'
            )

        deal_locks_total.labels(status='initiated').inc()
        _notify_seller_lock_initiated(deal, seller_token)
        _check_high_risk_deal(prop, deal)

        # AML screening — synchronous before response
        try:
            from apps.compliance.compliance_service import ComplianceService
            screening = ComplianceService.screen_deal_lock(deal, request.user)
            if screening.status == 'blocked':
                # deal was cancelled inside _handle_blocked
                from apps.compliance.models import SanctionScreeningResult
                sr = SanctionScreeningResult.objects.filter(deal_lock=deal).order_by('-screened_at').first()
                screening_id = str(sr.screening_id) if sr else None
                return Response(
                    {
                        'detail': 'Transaction blocked — compliance review required.',
                        'screening_id': screening_id,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        except Exception as _exc:
            logger.warning('DealLockInitiateView: AML screening fail-open: %s', _exc)

        payment_message = _get_payment_instructions(gateway, amount, deal.currency, org=org)

        return Response({
            'id':              str(deal.id),
            'status':          deal.status,
            'token_amount':    amount,
            'payment_gateway': gateway,
            'payment_message': payment_message,
            'message': (
                f"Deal lock requested for *{prop.title}*.\n\n"
                f"{payment_message}\n\n"
                "Once your payment is confirmed by our team, "
                "a 48-hour exclusivity window will begin."
            ),
        }, status=status.HTTP_201_CREATED)


class DealLockConfirmView(APIView):
    """PATCH /deals/lock/<id>/confirm/ — admin or org developer confirms payment."""
    permission_classes = [IsDashboardUser]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            if not org or deal.property.organization != org:
                return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        elif request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

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
        _notify_syndication_on_deal_lock(deal)

        return Response(EscrowDealSerializer(deal).data)


class DealLockCancelView(APIView):
    """PATCH /deals/lock/<id>/cancel/ — buyer, admin, or org developer cancels a lock."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        is_admin = request.user.role == 'admin'
        is_buyer = deal.buyer_id == request.user.pk
        is_org_admin = False
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            is_org_admin = bool(org and deal.property.organization == org)

        if not (is_admin or is_buyer or is_org_admin):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if deal.status not in (EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED):
            return Response(
                {'detail': f"Cannot cancel a deal in '{deal.status}' status."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deal.status = EscrowDeal.Status.CANCELLED
        deal.admin_notes = request.data.get('reason', deal.admin_notes)
        deal.save(update_fields=['status', 'admin_notes', 'updated_at'])
        deal_locks_total.labels(status='cancelled').inc()

        _notify_deal_cancelled(deal)

        return Response({'detail': 'Deal lock cancelled.', 'id': str(deal.id)})


class DealLockListView(generics.ListAPIView):
    """GET /deals/ — admin/agent/developer list of deal locks scoped by role."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [IsDashboardUser]

    def get_queryset(self):
        user = self.request.user
        qs = EscrowDeal.objects.select_related('property', 'buyer', 'seller', 'agent')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        if user.role == 'admin':
            return qs
        if user.role == 'agent':
            try:
                return qs.filter(agent=user.agent_profile)
            except Exception:
                return qs.none()
        if user.role == 'developer':
            org = get_user_org(user)
            if not org:
                return qs.none()
            return qs.filter(property__organization=org)
        return qs.none()


class MyDealLocksView(generics.ListAPIView):
    """GET /deals/mine/ — buyer's own deal locks."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return EscrowDeal.objects.filter(buyer=self.request.user).select_related(
            'property', 'buyer', 'seller', 'agent'
        )


class DealLockDetailView(generics.RetrieveAPIView):
    """GET /deals/lock/<id>/ — single deal lock detail, scoped by role."""
    serializer_class   = EscrowDealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base = EscrowDeal.objects.select_related('property', 'buyer', 'seller', 'agent')
        if user.role == 'admin':
            return base
        if user.role == 'agent':
            try:
                return base.filter(agent=user.agent_profile)
            except Exception:
                return base.none()
        if user.role == 'developer':
            org = get_user_org(user)
            if not org:
                return base.none()
            return base.filter(property__organization=org)
        # client: only their own purchases
        return base.filter(buyer=user)


class DealLockSellerConfirmView(APIView):
    """POST /deals/lock/<id>/seller-confirm/ — seller confirms awareness of deal lock."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)

        # Allow: the seller themselves, the assigned agent, or admin
        is_admin  = request.user.role == 'admin'
        _dev_org = get_user_org(request.user) if request.user.role == 'developer' else None
        is_seller = (
            deal.property.owner_id == request.user.pk
            or (
                request.user.role == 'developer'
                and deal.property.organization is not None
                and _dev_org == deal.property.organization
            )
        )
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
    """PATCH /deals/lock/<id>/release/ — admin or org developer marks deal completed."""
    permission_classes = [IsDashboardUser]

    def patch(self, request, pk):
        deal = get_object_or_404(EscrowDeal, pk=pk)
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            if not org or deal.property.organization != org:
                return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        elif request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if deal.status != EscrowDeal.Status.LOCKED:
            return Response(
                {'detail': f"Cannot release a deal in '{deal.status}' status. Only locked deals can be released."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deal.status = EscrowDeal.Status.RELEASED
        if request.data.get('admin_notes'):
            deal.admin_notes = request.data['admin_notes']
        deal.save(update_fields=['status', 'admin_notes', 'updated_at'])
        deal_locks_total.labels(status='released').inc()
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
        deal_locks_total.labels(status='disputed').inc()
        return Response({'detail': 'Deal flagged as disputed.', 'id': str(deal.id), 'status': deal.status})


def _notify_buyer_lock_active(deal: EscrowDeal):
    try:
        hrs = deal.hours_remaining()
        msg = (
            f"✅ *Deal Lock Confirmed!*\n\n"
            f"🏠 *Property:* {deal.property.title}\n"
            f"💰 *Token Amount:* {deal.currency} {deal.token_amount:,}\n"
            f"⏳ *Exclusivity:* {hrs:.0f} hours remaining\n"
            f"📅 *Expires:* {deal.lock_expires_at.strftime('%d %b %Y, %I:%M %p')}\n\n"
            "This property is now exclusively reserved for you. "
            "Contact your agent to proceed with the full transaction."
        )
        from apps.notifications.services import notify_user
        notify_user(deal.buyer, title="Deal Lock Confirmed", message=msg, event_type='deal_updates')
    except Exception as exc:
        logger.warning(f"Deal lock notify failed: {exc}")


def _notify_deal_cancelled(deal: EscrowDeal):
    """Notify seller and assigned agent when a deal lock is cancelled."""
    msg = (
        f"❌ *Deal Lock Cancelled*\n\n"
        f"🏠 *Property:* {deal.property.title}\n"
        f"💰 *Token Amount:* {deal.currency} {deal.token_amount:,}\n\n"
        "The deal lock on this property has been cancelled. "
        "The property is now available for new offers."
    )
    try:
        seller_user = deal.property.owner
        if seller_user and seller_user.phone:
            from apps.notifications.services import notify_user
            notify_user(seller_user, title="Deal Lock Cancelled", message=msg, event_type='deal_updates')
    except Exception as exc:
        logger.warning('_notify_deal_cancelled: seller notify failed: %s', exc)
    try:
        if deal.agent and getattr(deal.agent, 'user', None) and deal.agent.user.phone:
            from apps.notifications.services import notify_user
            notify_user(deal.agent.user, title="Deal Lock Cancelled", message=msg, event_type='deal_updates')
    except Exception as exc:
        logger.warning('_notify_deal_cancelled: agent notify failed: %s', exc)


def _notify_seller_deal_locked(deal: EscrowDeal):
    """Notify the property owner that online payment was confirmed and the lock is active."""
    try:
        seller_user = deal.property.owner
        if not seller_user or not seller_user.phone:
            return
        expires = deal.lock_expires_at.strftime('%d %b %Y, %I:%M %p') if deal.lock_expires_at else 'N/A'
        msg = (
            f"🔒 *Deal Lock Activated*\n\n"
            f"Online payment confirmed for your property:\n"
            f"🏠 *{deal.property.title}*\n"
            f"💰 *Token Amount:* {deal.currency} {deal.token_amount:,}\n"
            f"📅 *Lock Expires:* {expires}\n\n"
            "The buyer has paid the token amount. Contact them to proceed with the full transaction."
        )
        from apps.notifications.services import notify_user
        notify_user(seller_user, title="Deal Lock Activated", message=msg, event_type='deal_updates')
    except Exception as exc:
        logger.warning('_notify_seller_deal_locked: failed: %s', exc)


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
            f"💰 *Token Amount:* {deal.currency} {deal.token_amount:,}\n\n"
            f"Your confirmation code: *{token}*\n\n"
            "Please contact your agent or reply with your code to confirm. "
            "If you did not authorize this, contact support immediately."
        )
        WhatsAppClient.send_text(phone, msg)
    except Exception as exc:
        logger.warning(f"Seller deal lock notify failed: {exc}")


def _notify_syndication_on_deal_lock(deal: EscrowDeal):
    """Fire-and-forget: confirm any pending syndication commission for this deal's lead."""
    try:
        from apps.marketplace.models import SyndicationLeadSubmission
        from apps.marketplace.commission_ledger import CommissionLedger
        submission = (
            SyndicationLeadSubmission.objects
            .filter(lead=deal.buyer, status=SyndicationLeadSubmission.Status.ACCEPTED)
            .select_related('listing')
            .first()
        )
        if submission is None:
            return
        CommissionLedger().confirm_deal(deal_lock=deal, submission=submission)
        submission.status = SyndicationLeadSubmission.Status.CONVERTED
        SyndicationLeadSubmission.objects.filter(pk=submission.pk).update(
            status=SyndicationLeadSubmission.Status.CONVERTED
        )
    except Exception:
        logger.warning('Syndication deal-lock hook failed (non-fatal)', exc_info=True)
