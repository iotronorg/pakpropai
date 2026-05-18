import re
import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.core.throttles import BulkOperationThrottle

from .models import Lead, ConversationMessage, Appointment

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
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

        from django.db.models import Count

        dup_phones = (
            Lead.objects
            .values('user__phone')
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
            .values_list('user__phone', flat=True)
        )

        dup_leads = (
            Lead.objects
            .filter(user__phone__in=dup_phones)
            .select_related('user')
            .only('id', 'user__phone', 'status', 'intent', 'created_at')
            .order_by('user__phone', 'created_at')
        )

        groups: dict = {}
        for lead in dup_leads:
            norm = _normalize_phone(lead.user.phone)
            groups.setdefault(norm, []).append({
                'id':         str(lead.id),
                'phone':      lead.user.phone,
                'status':     lead.status,
                'intent':     lead.intent,
                'created_at': lead.created_at.isoformat(),
            })

        results = [
            {'normalized_phone': norm, 'leads': entries}
            for norm, entries in groups.items()
            if len(entries) > 1
        ]
        return Response({'count': len(results), 'results': results})


class MergeLeadsView(APIView):
    """
    POST /leads/merge/
    Merge a duplicate lead into a primary lead (admin only).
    Transfers all messages + appointments from secondary → primary, then deletes secondary.
    Body: { "primary_id": "<uuid>", "secondary_id": "<uuid>" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.role != 'admin':
            return Response({'detail': 'Admin access required.'}, status=403)

        primary_id   = request.data.get('primary_id')
        secondary_id = request.data.get('secondary_id')

        if not primary_id or not secondary_id:
            return Response({'detail': 'primary_id and secondary_id are required.'}, status=400)
        if str(primary_id) == str(secondary_id):
            return Response({'detail': 'primary_id and secondary_id must differ.'}, status=400)

        try:
            primary   = Lead.objects.select_related('user').get(pk=primary_id)
            secondary = Lead.objects.select_related('user').get(pk=secondary_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'One or both leads not found.'}, status=404)

        moved_msgs = ConversationMessage.objects.filter(lead=secondary).update(lead=primary)
        Appointment.objects.filter(lead=secondary).update(lead=primary)

        if secondary.notes:
            merged_notes = f"{primary.notes}\n\n[Merged from {secondary.user.phone}]:\n{secondary.notes}".strip()
            primary.notes = merged_notes

        if (secondary.score or 0) > (primary.score or 0):
            primary.score = secondary.score

        if not primary.assigned_agent and secondary.assigned_agent:
            primary.assigned_agent = secondary.assigned_agent

        primary.save(update_fields=['notes', 'score', 'assigned_agent'])

        secondary_phone = secondary.user.phone
        secondary.delete()

        return Response({
            'detail': f'Lead {secondary_phone} merged into {primary.user.phone}.',
            'primary_id': str(primary.id),
            'messages_transferred': moved_msgs,
        })


class BulkAssignLeadsView(APIView):
    """
    POST /leads/bulk-assign/
    Body: {"lead_ids": ["uuid1", ...], "agent_id": 123}
    Assigns multiple leads to one agent in a single call. Admin + developer only.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [BulkOperationThrottle]

    def post(self, request):
        if request.user.role not in ('admin', 'developer'):
            return Response({'error': 'Admin or developer access required.'},
                            status=status.HTTP_403_FORBIDDEN)

        lead_ids = request.data.get('lead_ids', [])
        agent_id = request.data.get('agent_id')

        if not lead_ids or not agent_id:
            return Response({'error': 'lead_ids (list) and agent_id are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        from apps.agents.models import Agent
        try:
            agent = Agent.objects.get(id=agent_id, is_active=True)
        except Agent.DoesNotExist:
            return Response({'error': 'Agent not found or inactive.'},
                            status=status.HTTP_404_NOT_FOUND)

        qs = Lead.objects.filter(id__in=lead_ids)

        if request.user.role == 'developer':
            try:
                org = request.user.owned_organization
                from django.db.models import Q as Qfilter
                qs = qs.filter(
                    Qfilter(organization=org) |
                    Qfilter(organization__isnull=True, assigned_agent__isnull=True)
                )
            except Exception:
                return Response({'error': 'Developer org not found.'},
                                status=status.HTTP_403_FORBIDDEN)

        count = qs.update(assigned_agent=agent)
        return Response({'assigned': count, 'agent_id': agent_id})
