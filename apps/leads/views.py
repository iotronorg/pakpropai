import logging
import re

from django.db.models import Avg
from django.utils import timezone
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from apps.core.idempotency import IDEMPOTENCY_PARAMETER, idempotent
from apps.core.permissions import get_user_org
from .models import Lead, LeadActivity, LeadScoreHistory, Appointment, ConversationMessage
from .serializers import (AppointmentSerializer, ConversationMessageSerializer,
                          LeadSerializer, LeadActivitySerializer, LeadScoreHistorySerializer)

logger = logging.getLogger(__name__)


class IsDashboardUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('admin', 'agent', 'developer')


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class   = LeadSerializer
    permission_classes = [IsDashboardUser]
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']

    @extend_schema(parameters=[IDEMPOTENCY_PARAMETER])
    @idempotent
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

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
            org = get_user_org(self.request.user)
            if not org:
                return qs.none()
            return qs.filter(organization=org)

        return qs.none()

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """GET /leads/stats/ — KPI summary scoped to caller's org/role."""
        qs = self.get_queryset()
        # T-1050c: "today" is the active org's local day (was a UTC date vs PKT buckets)
        from apps.markets.services import local_day_bounds, user_timezone
        day_start, day_end = local_day_bounds(user_timezone(request.user))
        hot_leads   = qs.filter(score__gte=70).count()
        unassigned  = qs.filter(assigned_agent__isnull=True).count()
        avg_score   = qs.aggregate(v=Avg('score'))['v']
        new_today   = qs.filter(created_at__gte=day_start, created_at__lt=day_end).count()
        return Response({
            'hot_leads':  hot_leads,
            'unassigned': unassigned,
            'avg_score':  round(avg_score, 1) if avg_score is not None else None,
            'new_today':  new_today,
        })

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
            if request.user.role == 'admin':
                agent = Agent.objects.get(id=agent_id, is_active=True)
            else:
                agent = Agent.objects.get(id=agent_id, is_active=True, organization=lead.organization)
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
        msgs = lead.crm_messages().select_related('sender').order_by('created_at')
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
            from apps.whatsapp.client import get_wa_client
            resp  = get_wa_client(lead.organization).send_text(phone, body, skip_window_check=True)
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

    @action(detail=True, methods=['post'], url_path='summarize')
    def summarize(self, request, pk=None):
        """
        POST /leads/{id}/summarize/
        Returns an AI-written summary of the lead's conversation history.
        """
        lead = self.get_object()
        msgs = list(
            lead.crm_messages().order_by('-created_at')[:30]
        )[::-1]  # last 30 messages, oldest first

        if not msgs:
            return Response({'summary': 'No conversation history yet.'})

        transcript = '\n'.join(
            f"{'Client' if m.direction == 'inbound' else 'Agent'}: {m.body}"
            for m in msgs
        )
        # T-1120: registered prompt, market from the lead's org; the phone number
        # is no longer sent to the model (it added nothing to the summary).
        try:
            from apps.ai import llm
            summary = llm.complete('lead.summary', {
                'status': lead.status, 'budget_min': lead.budget_min or '?',
                'budget_max': lead.budget_max or '?', 'city': lead.city_interest or 'unknown',
                'count': len(msgs), 'transcript': transcript,
            }, country=getattr(lead.organization, 'country', None), user=request.user,
                interaction_type='lead_summary', max_tokens=400, temperature=0.3).text
        except Exception as exc:
            logger.error(f"Summarize failed for lead {lead.id}: {exc}")
            return Response({'detail': 'AI summarization unavailable.'}, status=503)

        return Response({'summary': summary, 'message_count': len(msgs)})

    @action(detail=True, methods=['post'], url_path='suggest-replies')
    def suggest_replies(self, request, pk=None):
        """
        POST /leads/{id}/suggest-replies/
        Returns 3 context-aware reply suggestions for the agent.
        """
        lead = self.get_object()
        msgs = list(lead.crm_messages().order_by('-created_at')[:10])[::-1]

        if not msgs:
            return Response({'suggestions': []})

        last_inbound = next(
            (m.body for m in reversed(msgs) if m.direction == 'inbound'), None
        )
        if not last_inbound:
            return Response({'suggestions': []})

        recent = '\n'.join(
            f"{'Client' if m.direction == 'inbound' else 'Agent'}: {m.body}"
            for m in msgs[-6:]
        )
        try:
            from apps.ai import llm
            raw = llm.complete('lead.suggest_replies', {
                'status': lead.status, 'city': lead.city_interest or 'unknown',
                'budget_max': lead.budget_max or '?', 'recent': recent, 'last_inbound': last_inbound,
            }, country=getattr(lead.organization, 'country', None), user=request.user,
                interaction_type='reply_suggest', max_tokens=300, temperature=0.6).text
            suggestions = llm.parse_json(raw)
            if not isinstance(suggestions, list):
                suggestions = []
            suggestions = [str(s) for s in suggestions[:3]]
        except Exception as exc:
            logger.error(f"suggest-replies failed for lead {lead.id}: {exc}")
            suggestions = []

        return Response({'suggestions': suggestions})

    @action(detail=True, methods=['get'], url_path='activities')
    def activities(self, request, pk=None):
        """GET /leads/{id}/activities/ — chronological activity log for this lead."""
        lead = self.get_object()
        qs = LeadActivity.objects.filter(lead=lead).select_related('actor').order_by('-created_at')
        return Response(LeadActivitySerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='score-history')
    def score_history(self, request, pk=None):
        """GET /leads/{id}/score-history/ — intent score change log for this lead."""
        lead = self.get_object()
        qs = LeadScoreHistory.objects.filter(lead=lead).select_related('changed_by').order_by('-created_at')
        return Response(LeadScoreHistorySerializer(qs, many=True).data)


class AppointmentViewSet(viewsets.ModelViewSet):
    serializer_class   = AppointmentSerializer
    permission_classes = [IsDashboardUser]

    @extend_schema(parameters=[IDEMPOTENCY_PARAMETER])
    @idempotent
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

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
            org = get_user_org(self.request.user)
            if not org:
                return qs.none()
            qs = qs.filter(agent__organization=org)
        # admin sees all

        params = self.request.query_params
        if status_f := params.get('status'):
            qs = qs.filter(status=status_f)
        if lead_id := params.get('lead'):
            qs = qs.filter(lead_id=lead_id)
        if params.get('upcoming') in ('true', '1'):
            qs = qs.filter(
                scheduled_at__gte=timezone.now(),
                status__in=[Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED],
            ).order_by('scheduled_at')
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
            from apps.markets.services import format_local
            scheduled  = format_local(appt.scheduled_at, appt.lead.organization, '%d %b %Y %H:%M')
            if new_status == 'confirmed':
                notify_user(
                    appt.lead.user,
                    title="Appointment Confirmed",
                    message=f"✅ Your appointment for {prop_title} on {scheduled} has been confirmed.",
                    event_type='appointment_reminders',
                )
            elif new_status == 'cancelled':
                notify_user(
                    appt.lead.user,
                    title="Appointment Cancelled",
                    message=f"❌ Your appointment for {prop_title} on {scheduled} has been cancelled.",
                    event_type='appointment_reminders',
                )
            elif new_status == 'rescheduled':
                notify_user(
                    appt.lead.user,
                    title="Appointment Rescheduled",
                    message=f"🔄 Your appointment for {prop_title} has been rescheduled to {scheduled}.",
                    event_type='appointment_reminders',
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


