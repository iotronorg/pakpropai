from rest_framework.exceptions import APIException
from rest_framework import permissions, status

from .ledger import UsageLedger


class PlanLimitExceeded(APIException):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = 'Plan limit reached. Upgrade your plan.'
    default_code = 'plan_limit_exceeded'

    def __init__(self, detail=None):
        super().__init__(
            detail={
                'detail': detail or self.default_detail,
                'code': self.default_code,
            }
        )


class WithinAgentSeatLimit(permissions.BasePermission):
    """
    DRF permission that enforces the agent seat limit for the requesting org.
    Raises HTTP 402 (PlanLimitExceeded) when the limit is reached.
    Only checked on unsafe methods (POST).
    """

    def has_permission(self, request, view):
        if request.method not in ('POST',):
            return True

        user = request.user
        if not user or not user.is_authenticated:
            return True  # let authentication handle this

        if user.role == 'admin':
            return True  # platform admin is unrestricted

        try:
            org  = user.owned_organization
            plan = getattr(org, 'plan', 'trial')
            org_id = str(org.id)
        except Exception:
            return True  # no org linked — let view handle it

        if not UsageLedger.within_limit(org_id, plan, 'agents'):
            raise PlanLimitExceeded(
                'Agent seat limit reached. Upgrade your plan.'
            )

        return True
