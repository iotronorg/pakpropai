import datetime
import logging

from django.db.models import Count, Avg
from django.db.models.functions import TruncWeek, TruncMonth
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Report


def _get_trend(qs, created_field='created_at', period='weekly'):
    """Return [{period, count}] for the last 8 weeks or 6 months."""
    now = timezone.now()
    if period == 'monthly':
        cutoff = now - datetime.timedelta(days=182)
        rows = (
            qs.filter(**{f'{created_field}__gte': cutoff})
              .annotate(bucket=TruncMonth(created_field))
              .values('bucket')
              .annotate(count=Count('id'))
              .order_by('bucket')
        )
        return [{'period': r['bucket'].strftime('%Y-%m'), 'count': r['count']} for r in rows]
    else:
        cutoff = now - datetime.timedelta(weeks=8)
        rows = (
            qs.filter(**{f'{created_field}__gte': cutoff})
              .annotate(bucket=TruncWeek(created_field))
              .values('bucket')
              .annotate(count=Count('id'))
              .order_by('bucket')
        )
        return [{'period': r['bucket'].strftime('%Y-%m-%d'), 'count': r['count']} for r in rows]

logger = logging.getLogger(__name__)


class IsAdminOrDeveloper(IsAuthenticated):
    def has_permission(self, request, view):
        return (super().has_permission(request, view)
                and request.user.role in ('admin', 'developer'))


class ReportGenerateView(APIView):
    """POST /reports/generate/ — create a report and queue async generation."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        report_type = request.data.get('report_type', '')
        if report_type not in Report.ReportType.values:
            return Response(
                {'detail': f"Invalid report_type. Choose from: {Report.ReportType.values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        property_id = request.data.get('property_id')
        prop = None
        if property_id:
            from apps.properties.models import Property
            try:
                prop = Property.objects.get(id=property_id)
            except Property.DoesNotExist:
                return Response({'detail': 'Property not found.'},
                                status=status.HTTP_404_NOT_FOUND)

        if report_type == 'property_analysis' and not prop:
            return Response({'detail': 'property_id is required for property_analysis.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Store caller-supplied input params inside content['input'] for task to read
        input_params = {k: v for k, v in request.data.items()
                        if k not in ('report_type', 'property_id')}

        report = Report.objects.create(
            user=request.user,
            property=prop,
            report_type=report_type,
            status=Report.Status.PENDING,
            content={'input': input_params},
        )

        from .tasks import generate_report_task
        generate_report_task.delay(str(report.id))

        return Response({
            'id':          str(report.id),
            'report_type': report.report_type,
            'status':      report.status,
            'created_at':  report.created_at.isoformat(),
        }, status=status.HTTP_202_ACCEPTED)


class ReportStatusView(APIView):
    """GET /reports/<id>/ — poll status of a report."""
    permission_classes = [IsAuthenticated]

    def get(self, request, report_id):
        try:
            report = Report.objects.get(id=report_id)
        except Report.DoesNotExist:
            return Response({'detail': 'Report not found.'}, status=status.HTTP_404_NOT_FOUND)

        if report.user != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            'id':          str(report.id),
            'report_type': report.report_type,
            'status':      report.status,
            'file_url':    report.file_url or None,
            'created_at':  report.created_at.isoformat(),
            'ready_at':    report.ready_at.isoformat() if report.ready_at else None,
        })


class ReportDownloadView(APIView):
    """GET /reports/<id>/download/ — redirect to PDF or return inline content."""
    permission_classes = [IsAuthenticated]

    def get(self, request, report_id):
        try:
            report = Report.objects.get(id=report_id)
        except Report.DoesNotExist:
            return Response({'detail': 'Report not found.'}, status=status.HTTP_404_NOT_FOUND)

        if report.user != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if report.status != Report.Status.READY:
            return Response(
                {'detail': f"Report is not ready yet (status: {report.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        if not report.file_url:
            return Response({'detail': 'File URL not available.'},
                            status=status.HTTP_404_NOT_FOUND)

        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(report.file_url)


class MyReportsView(APIView):
    """GET /reports/mine/ — authenticated user's own report history."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reports = Report.objects.filter(user=request.user).order_by('-created_at')[:50]
        return Response([{
            'id':          str(r.id),
            'report_type': r.report_type,
            'status':      r.status,
            'file_url':    r.file_url or None,
            'created_at':  r.created_at.isoformat(),
            'ready_at':    r.ready_at.isoformat() if r.ready_at else None,
        } for r in reports])


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

        result = {
            'total':     qs.count(),
            'avg_score': round(avg_score, 1),
            'hot_leads': hot_leads,
            'by_status': status_counts,
            'by_intent': intent_counts,
            'by_source': source_counts,
        }
        period = request.query_params.get('period')
        if period in ('weekly', 'monthly'):
            result['trend'] = _get_trend(qs, 'created_at', period)
        return Response(result)


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

        result = {
            'total':                 qs.count(),
            'avg_ai_score':          round(avg_score, 1),
            'installment_available': qs.filter(installment_available=True).count(),
            'by_type':               by_type,
            'by_legal_status':       by_legal,
            'by_risk_level':         by_risk,
            'by_city':               by_city,
        }
        period = request.query_params.get('period')
        if period in ('weekly', 'monthly'):
            result['trend'] = _get_trend(qs, 'created_at', period)
        return Response(result)
