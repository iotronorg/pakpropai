import logging
import re

from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Lead, Appointment, ConversationMessage
from .serializers import (AppointmentSerializer, ConversationMessageSerializer,
                          LeadSerializer)

logger = logging.getLogger(__name__)


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

    @action(detail=True, methods=['get'], url_path='suggest-agents')
    def suggest_agents(self, request, pk=None):
        """GET /leads/{id}/suggest-agents/ — top 3 agent candidates for this lead."""
        lead = self.get_object()
        from .services import suggest_agents_for_lead
        from apps.agents.serializers import AgentSerializer
        agents = suggest_agents_for_lead(lead, limit=3)
        return Response(AgentSerializer(agents, many=True).data)

    @action(detail=True, methods=['post'], url_path='assign')
    def assign(self, request, pk=None):
        """POST /leads/{id}/assign/ — assign an agent to this lead."""
        if request.user.role not in ('admin', 'developer'):
            return Response({'detail': 'Admin or developer access required.'},
                            status=status.HTTP_403_FORBIDDEN)
        lead = self.get_object()
        agent_id = request.data.get('agent_id')
        if not agent_id:
            return Response({'detail': 'agent_id is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        from apps.agents.models import Agent
        try:
            agent = Agent.objects.get(id=agent_id, is_active=True)
        except Agent.DoesNotExist:
            return Response({'detail': 'Agent not found or inactive.'},
                            status=status.HTTP_404_NOT_FOUND)
        from .services import assign_agent_to_lead
        assign_agent_to_lead(lead, agent, actor=request.user)
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=['post'], url_path='auto-assign')
    def auto_assign(self, request, pk=None):
        """POST /leads/{id}/auto-assign/ — system picks and assigns the best agent."""
        if request.user.role not in ('admin', 'developer'):
            return Response({'detail': 'Admin or developer access required.'},
                            status=status.HTTP_403_FORBIDDEN)
        lead = self.get_object()
        from .services import suggest_agents_for_lead, assign_agent_to_lead
        candidates = suggest_agents_for_lead(lead, limit=1)
        if not candidates:
            return Response({'detail': 'No suitable agent found for this lead.'},
                            status=status.HTTP_404_NOT_FOUND)
        assign_agent_to_lead(lead, candidates[0], actor=request.user)
        return Response(LeadSerializer(lead).data)

    @action(detail=True, methods=['get'], url_path='conversations')
    def conversations(self, request, pk=None):
        """GET /leads/{id}/conversations/ — full message history for a lead."""
        lead = self.get_object()
        msgs = lead.messages.select_related('sender').order_by('created_at')
        serializer = ConversationMessageSerializer(msgs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='send-message')
    def send_message(self, request, pk=None):
        """POST /leads/{id}/send-message/ — agent sends a WhatsApp message to the lead."""
        lead = self.get_object()
        body = (request.data.get('body') or '').strip()
        if not body:
            return Response({'detail': 'body is required.'}, status=status.HTTP_400_BAD_REQUEST)

        phone = lead.user.phone.lstrip('+')
        wa_id = ''
        try:
            from apps.whatsapp.client import WhatsAppClient
            resp  = WhatsAppClient.send_text(phone, body, skip_window_check=True)
            wa_id = (resp.get('messages') or [{}])[0].get('id', '')
        except Exception as exc:
            logger.warning(f"WhatsApp send failed for lead {lead.id}: {exc}")

        msg = ConversationMessage.objects.create(
            lead=lead,
            direction=ConversationMessage.Direction.OUTBOUND,
            channel=ConversationMessage.Channel.WHATSAPP,
            body=body,
            sender=request.user,
            wa_message_id=wa_id,
        )

        lead.last_contacted_at = timezone.now()
        lead.save(update_fields=['last_contacted_at'])

        return Response(ConversationMessageSerializer(msg).data,
                        status=status.HTTP_201_CREATED)


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

    def _transition(self, request, pk, allowed_from, new_status, extra=None):
        appt = self.get_object()
        if appt.status not in allowed_from:
            return Response(
                {'detail': f"Cannot transition from '{appt.status}' to '{new_status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        fields = {'status': new_status, **(extra or {})}
        for k, v in fields.items():
            setattr(appt, k, v)
        appt.save(update_fields=list(fields.keys()) + ['updated_at'])
        self._notify_appointment(appt, new_status)
        return Response(AppointmentSerializer(appt).data)

    @staticmethod
    def _notify_appointment(appt, new_status):
        try:
            from apps.notifications.services import notify_user
            prop_title = appt.property.title if appt.property else "Property visit"
            scheduled  = appt.scheduled_at.strftime('%d %b %Y %H:%M')
            if new_status == 'confirmed':
                notify_user(
                    appt.lead.user,
                    title="Appointment Confirmed",
                    message=f"✅ Your appointment for {prop_title} on {scheduled} has been confirmed.",
                )
            elif new_status == 'cancelled':
                notify_user(
                    appt.lead.user,
                    title="Appointment Cancelled",
                    message=f"❌ Your appointment for {prop_title} on {scheduled} has been cancelled.",
                )
            elif new_status == 'rescheduled':
                notify_user(
                    appt.lead.user,
                    title="Appointment Rescheduled",
                    message=f"🔄 Your appointment for {prop_title} has been rescheduled to {scheduled}.",
                )
        except Exception:
            pass

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        return self._transition(request, pk,
                                allowed_from={Appointment.Status.SCHEDULED},
                                new_status=Appointment.Status.CONFIRMED)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return self._transition(request, pk,
                                allowed_from={Appointment.Status.SCHEDULED,
                                              Appointment.Status.CONFIRMED,
                                              Appointment.Status.RESCHEDULED},
                                new_status=Appointment.Status.CANCELLED)

    @action(detail=True, methods=['post'])
    def reschedule(self, request, pk=None):
        new_time = request.data.get('scheduled_at')
        if not new_time:
            return Response({'detail': 'scheduled_at is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        from django.utils.dateparse import parse_datetime
        dt = parse_datetime(new_time)
        if not dt:
            return Response({'detail': 'Invalid datetime format. Use ISO 8601.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return self._transition(request, pk,
                                allowed_from={Appointment.Status.SCHEDULED,
                                              Appointment.Status.CONFIRMED},
                                new_status=Appointment.Status.RESCHEDULED,
                                extra={'scheduled_at': dt})

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return self._transition(request, pk,
                                allowed_from={Appointment.Status.CONFIRMED,
                                              Appointment.Status.SCHEDULED},
                                new_status=Appointment.Status.COMPLETED)
