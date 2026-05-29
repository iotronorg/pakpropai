from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class ComplianceService:

    @staticmethod
    def screen_deal_lock(deal, buyer_user) -> 'ScreeningResult':
        """
        AML screen buyer for a deal lock.
        On blocked: cancels deal, notifies org admin, blocks WhatsApp sessions.
        Fail-open: any exception → clear result.
        """
        from .aml_gateway import AMLScreeningGateway, ScreeningResult

        try:
            org         = getattr(deal.property, 'organization', None)
            buyer_name  = getattr(buyer_user, 'name', '') or ''
            buyer_phone = getattr(buyer_user, 'phone', '') or ''
            buyer_cnic  = getattr(buyer_user, 'cnic', '') or ''

            result = AMLScreeningGateway.screen_entity(
                name=buyer_name,
                id_number=buyer_cnic or buyer_phone,
                id_type='cnic' if buyer_cnic else 'phone',
                org=org,
            )

            # Link the persisted SanctionScreeningResult to this deal
            if result.status != 'clear':
                try:
                    from .models import SanctionScreeningResult
                    SanctionScreeningResult.objects.filter(
                        screened_name=buyer_name,
                        deal_lock__isnull=True,
                    ).order_by('-screened_at').first() and \
                    SanctionScreeningResult.objects.filter(
                        screened_name=buyer_name,
                        deal_lock__isnull=True,
                    ).order_by('-screened_at').update(deal_lock=deal)
                except Exception:
                    pass

            if result.status == 'blocked':
                ComplianceService._handle_blocked(deal, buyer_user, result, org)

            return result

        except Exception as exc:
            logger.warning('ComplianceService.screen_deal_lock fail-open: %s', exc)
            from .aml_gateway import ScreeningResult
            return ScreeningResult(status='clear', risk_score=0)

    @staticmethod
    def _handle_blocked(deal, buyer_user, result, org) -> None:
        from apps.escrow.models import EscrowDeal

        # 1. Cancel the deal
        try:
            deal.status = EscrowDeal.Status.CANCELLED
            deal.save(update_fields=['status'])
        except Exception as exc:
            logger.error('ComplianceService: cancel blocked deal failed: %s', exc)

        # 2. Admin notification
        try:
            from apps.notifications.models import Notification
            admin_user = getattr(org, 'admin_user', None) if org else None
            if admin_user:
                Notification.objects.create(
                    user=admin_user,
                    title='AML Block — Deal Lock Cancelled',
                    message=(
                        f"Deal lock {deal.id} was automatically cancelled — AML screening blocked "
                        f"buyer {getattr(buyer_user, 'phone', 'unknown')}. "
                        f"Risk score: {result.risk_score}. Please review."
                    ),
                )
        except Exception as exc:
            logger.error('ComplianceService: admin notification failed: %s', exc)

        # 3. Audit log
        try:
            from apps.core.models import AuditLog
            AuditLog.objects.create(
                action=AuditLog.Action.REJECT,
                target_model='EscrowDeal',
                target_id=str(deal.id),
                detail=f'aml_block — status={result.status} risk_score={result.risk_score}',
            )
        except Exception as exc:
            logger.error('ComplianceService: audit log failed: %s', exc)

        # 4. Block WhatsApp sessions for this buyer
        try:
            from apps.whatsapp.models import WhatsAppSession
            buyer_phone = getattr(buyer_user, 'phone', None)
            if buyer_phone:
                WhatsAppSession.objects.filter(phone=buyer_phone).update(
                    conversation_mode=WhatsAppSession.ConversationMode.BLOCKED,
                )
        except Exception as exc:
            logger.error('ComplianceService: block WA sessions failed: %s', exc)

    @staticmethod
    def calculate_deal_tax(deal, org):
        """Returns FBRTaxResult for the deal based on org country. Fail-open."""
        try:
            from apps.markets.registry import get_tax_calculator
            country = (getattr(org, 'country', '') or 'PK').strip() or 'PK'
            calc    = get_tax_calculator(country)
            amount  = int(getattr(deal, 'token_amount', 0) or 0)
            return calc.calculate_withholding_tax(amount=amount)
        except Exception as exc:
            logger.warning('ComplianceService.calculate_deal_tax fail-open: %s', exc)
            from apps.markets.pk.tax import FBRTaxResult
            return FBRTaxResult(country='', supported=False)
