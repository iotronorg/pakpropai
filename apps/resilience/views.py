import logging
from django.utils.timezone import now
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.core.permissions import IsAdminUser

from .resilience_engine import AdvancedCircuitBreaker, OutageFlagService, _REGISTRY
from .queue_monitor import _get_isolated_orgs

logger = logging.getLogger(__name__)


class SlaStatusView(APIView):
    """
    GET /api/v1/sla/status/
    Admin-only. Returns live circuit state, outage flags, and queue isolation info.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from django.core.cache import cache

        # Isolated orgs: org_ids with an active aux_route:{org_id} key
        isolated_orgs = _get_isolated_orgs()

        return Response({
            'circuits': AdvancedCircuitBreaker.get_all_states(),
            'outages':  OutageFlagService.get_all_outages(),
            'queue_isolation': {
                'isolated_orgs': isolated_orgs,
                'total_monitored': len(_REGISTRY),
            },
            'as_of': now().isoformat(),
        })


class SlaCircuitResetView(APIView):
    """
    POST /api/v1/sla/circuits/{service}/reset/
    Admin-only. Force-resets a named circuit to CLOSED.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, service: str):
        circuit = _REGISTRY.get(service)
        if circuit is None:
            return Response(
                {'detail': f'Unknown service: {service}. Valid: {list(_REGISTRY.keys())}'},
                status=404,
            )
        circuit.reset()
        return Response({'service': service, 'state': circuit.get_state()})
