import re
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Lead, Appointment
from .serializers import LeadSerializer, AppointmentSerializer


def _normalize_phone(phone: str) -> str:
    """Strip non-digits, then normalise Pakistan numbers to 03XXXXXXXXX form."""
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('923') and len(digits) == 12:
        return '0' + digits[2:]
    if digits.startswith('92') and len(digits) == 11:
        return '0' + digits[2:]
    return digits


class DuplicateLeadView(APIView):
    """
    GET /leads/duplicates/
    Admin-only: find lead pairs whose phone numbers normalize to the same value.
    These may represent the same real person entered via different flows.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        leads = (
            Lead.objects
            .select_related('user', 'assigned_agent')
            .only('id', 'user__phone', 'user__name', 'status', 'intent', 'created_at')
            .order_by('created_at')
        )

        seen: dict = {}
        duplicates = []
        for lead in leads:
            norm = _normalize_phone(lead.user.phone)
            if norm in seen:
                duplicates.append({
                    'normalized_phone': norm,
                    'leads': [
                        {
                            'id':    str(seen[norm].id),
                            'phone': seen[norm].user.phone,
                            'status': seen[norm].status,
                            'intent': seen[norm].intent,
                            'created_at': seen[norm].created_at.isoformat(),
                        },
                        {
                            'id':    str(lead.id),
                            'phone': lead.user.phone,
                            'status': lead.status,
                            'intent': lead.intent,
                            'created_at': lead.created_at.isoformat(),
                        },
                    ],
                })
            else:
                seen[norm] = lead

        return Response({'count': len(duplicates), 'results': duplicates})


class IsDashboardUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('admin', 'agent', 'developer')


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class   = LeadSerializer
    permission_classes = [IsDashboardUser]
    http_method_names  = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = Lead.objects.select_related('user', 'assigned_agent')
        role = self.request.user.role

        if role == 'admin':
            return qs.all()

        if role == 'agent':
            try:
                return qs.filter(assigned_agent=self.request.user.agent_profile)
            except Exception:
                return qs.none()

        if role == 'developer':
            # Developers see leads assigned to any agent inside their org.
            # Org members are Agent records whose parent_organization == this user's agent profile.
            try:
                org = self.request.user.agent_profile
                return qs.filter(
                    assigned_agent__parent_organization=org
                )
            except Exception:
                return qs.none()

        return qs.none()


class AppointmentViewSet(viewsets.ModelViewSet):
    serializer_class   = AppointmentSerializer
    permission_classes = [IsDashboardUser]

    def get_queryset(self):
        qs = Appointment.objects.select_related(
            'lead__user', 'property', 'agent', 'created_by'
        )
        role = self.request.user.role
        if role == 'agent':
            try:
                qs = qs.filter(agent=self.request.user.agent_profile)
            except Exception:
                return qs.none()
        elif role == 'developer':
            try:
                org = self.request.user.agent_profile
                qs = qs.filter(agent__parent_organization=org)
            except Exception:
                return qs.none()
        # admin sees all

        params = self.request.query_params
        if status_f := params.get('status'):
            qs = qs.filter(status=status_f)
        if lead_id := params.get('lead'):
            qs = qs.filter(lead_id=lead_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
