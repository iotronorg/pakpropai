import logging
from datetime import datetime

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ledger import UsageLedger
from .limits import PLAN_LIMITS

logger = logging.getLogger(__name__)


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
