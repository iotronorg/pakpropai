from django.db.models import Count, Avg
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class IsAdminOrDeveloper(IsAuthenticated):
    def has_permission(self, request, view):
        return (super().has_permission(request, view)
                and request.user.role in ('admin', 'developer'))


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


class LeadReportView(APIView):
    """GET /reports/leads/ — lead funnel and conversion analytics."""
    permission_classes = [IsAdminOrDeveloper]

    def get(self, request):
        from apps.leads.models import Lead

        qs = Lead.objects.all()
        if request.user.role == 'developer':
            try:
                org = request.user.agent_profile
                qs = qs.filter(assigned_agent__parent_organization=org)
            except Exception:
                qs = qs.none()

        status_counts = dict(
            qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
        )
        intent_counts = dict(
            qs.values_list('intent').annotate(c=Count('id')).values_list('intent', 'c')
        )
        source_counts = dict(
            qs.values_list('source').annotate(c=Count('id')).values_list('source', 'c')
        )
        avg_score = qs.aggregate(avg=Avg('score'))['avg'] or 0
        hot_leads = qs.filter(score__gte=70).count()

        return Response({
            'total':     qs.count(),
            'avg_score': round(avg_score, 1),
            'hot_leads': hot_leads,
            'by_status': status_counts,
            'by_intent': intent_counts,
            'by_source': source_counts,
        })


class AgentReportView(APIView):
    """GET /reports/agents/ — agent performance summary. Admin only."""
    permission_classes = [IsAdmin]

    def get(self, request):
        from apps.agents.models import Agent
        from apps.leads.models import Lead

        agents = Agent.objects.filter(is_active=True).select_related('user')
        data = []
        for agent in agents:
            lead_count   = Lead.objects.filter(assigned_agent=agent).count()
            closed_count = Lead.objects.filter(
                assigned_agent=agent, status='qualified'
            ).count()
            data.append({
                'id':           agent.id,
                'name':         agent.name,
                'phone':        agent.phone,
                'is_verified':  agent.is_verified,
                'total_leads':  lead_count,
                'closed_leads': closed_count,
                'closed_deals': agent.closed_deals,
                'rating':       float(agent.rating),
                'primary_city': agent.primary_city,
            })

        data.sort(key=lambda a: a['total_leads'], reverse=True)
        return Response({'count': len(data), 'results': data})


class PropertyReportView(APIView):
    """GET /reports/properties/ — property inventory stats."""
    permission_classes = [IsAdminOrDeveloper]

    def get(self, request):
        from apps.properties.models import Property

        qs = Property.objects.filter(is_active=True)
        if request.user.role == 'developer':
            qs = qs.filter(owner=request.user)

        by_type   = dict(qs.values_list('property_type').annotate(c=Count('id')).values_list('property_type', 'c'))
        by_legal  = dict(qs.values_list('legal_status').annotate(c=Count('id')).values_list('legal_status', 'c'))
        by_risk   = dict(qs.values_list('risk_level').annotate(c=Count('id')).values_list('risk_level', 'c'))
        by_city   = dict(qs.values_list('city').annotate(c=Count('id')).values_list('city', 'c'))
        avg_score = qs.aggregate(avg=Avg('ai_score'))['avg'] or 0

        return Response({
            'total':                 qs.count(),
            'avg_ai_score':          round(avg_score, 1),
            'installment_available': qs.filter(installment_available=True).count(),
            'by_type':               by_type,
            'by_legal_status':       by_legal,
            'by_risk_level':         by_risk,
            'by_city':               by_city,
        })
