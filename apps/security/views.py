from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminUser
from apps.security.models import ApiSecurityEvent
from apps.security.serializers import ApiSecurityEventSerializer


class SecurityEventListView(ListAPIView):
    serializer_class   = ApiSecurityEventSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        qs         = ApiSecurityEvent.objects.all()
        event_type = self.request.query_params.get('event_type')
        ip         = self.request.query_params.get('ip')
        org_id     = self.request.query_params.get('org_id')
        if event_type:
            qs = qs.filter(event_type=event_type)
        if ip:
            qs = qs.filter(ip_address=ip)
        if org_id:
            qs = qs.filter(organization_id=org_id)
        return qs.order_by('-created_at')


class ChainVerifyView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        limit = int(request.query_params.get('limit', 1000))
        ok, msg = ApiSecurityEvent.verify_chain(limit=limit)
        return Response({'valid': ok, 'message': msg, 'records_checked': limit})
