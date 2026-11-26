from apps.properties.taxonomy import property_type_expression  # T-1070g
import datetime
import logging

from django.core.exceptions import ValidationError
from django.db.models import Count, Avg, Sum
from django.db.models.functions import TruncWeek, TruncMonth, TruncDay
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from apps.core.throttles import ReportGenerateThrottle
from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin, IsAgentOrAdmin, get_user_org
from apps.markets.services import user_timezone
from .models import Report, MonthlyReport


def _get_trend(qs, created_field='created_at', period='weekly', tz=None):
    """Return [{period, count}] for the last 8 weeks or 6 months, bucketed in ``tz`` (T-1050c)."""
    now = timezone.now()
    if period == 'monthly':
        cutoff = now - datetime.timedelta(days=182)
        rows = (
            qs.filter(**{f'{created_field}__gte': cutoff})
              .annotate(bucket=TruncMonth(created_field, tzinfo=tz))
              .values('bucket')
              .annotate(count=Count('id'))
              .order_by('bucket')
        )
        return [{'period': r['bucket'].strftime('%Y-%m'), 'count': r['count']} for r in rows]
    else:
        cutoff = now - datetime.timedelta(weeks=8)
        rows = (
            qs.filter(**{f'{created_field}__gte': cutoff})
              .annotate(bucket=TruncWeek(created_field, tzinfo=tz))
              .values('bucket')
              .annotate(count=Count('id'))
              .order_by('bucket')
        )
        return [{'period': r['bucket'].strftime('%Y-%m-%d'), 'count': r['count']} for r in rows]

logger = logging.getLogger(__name__)


def _may_report_on(user, prop) -> bool:
    """A listing the person manages, one of their organization, or a public one."""
    from apps.properties.lifecycle import can_manage
    from apps.properties.models import Property
    if can_manage(user, prop) or Property.objects.visible_to(None).filter(pk=prop.pk).exists():
        return True
    org = get_user_org(user) if user.role in ('developer', 'agent') else None
    return org is not None and prop.organization_id == org.pk


class ReportGenerateView(APIView):
    """POST /reports/generate/ — create a report and queue async generation."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ReportGenerateThrottle]

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
            except (Property.DoesNotExist, ValidationError, ValueError, TypeError):
                prop = None
            if prop is None or not _may_report_on(request.user, prop):
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


class AgentPersonalReportView(APIView):
    """GET /reports/my-stats/ — personal performance report for the logged-in agent."""
    permission_classes = [IsAgentOrAdmin]

    def get(self, request):
        from apps.leads.models import Lead
        from apps.properties.models import Property

        try:
            agent = request.user.agent_profile
        except Exception:
            return Response({'detail': 'No agent profile found.'}, status=status.HTTP_404_NOT_FOUND)

        leads = Lead.objects.filter(assigned_agent=agent)
        props = Property.objects.filter(owner=request.user, is_active=True)

        status_counts = dict(
            leads.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
        )
        source_counts = dict(
            leads.values_list('source').annotate(c=Count('id')).values_list('source', 'c')
        )
        avg_score = leads.aggregate(avg=Avg('score'))['avg'] or 0

        from apps.agents.stats import _compute_response_time
        lead_ids = list(leads.values_list('id', flat=True))

        result = {
            'total_leads':             len(lead_ids),
            'closed_leads':            leads.filter(routing_state=Lead.RoutingState.CLOSED).count(),
            'hot_leads':               leads.filter(score__gte=70).count(),
            'avg_score':               round(avg_score, 1),
            'by_status':               status_counts,
            'by_source':               source_counts,
            'total_listings':          props.count(),
            'closed_deals':            agent.closed_deals,
            'rating':                  float(agent.rating),
            'is_verified':             agent.is_verified,
            'avg_response_time_hours': _compute_response_time(lead_ids),
        }

        period = request.query_params.get('period')
        if period in ('weekly', 'monthly'):
            result['trend'] = _get_trend(leads, 'created_at', period, user_timezone(request.user))

        return Response(result)


class LeadReportView(APIView):
    """GET /reports/leads/ — lead funnel and conversion analytics."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from apps.leads.models import Lead

        qs = Lead.objects.all()
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            qs = qs.filter(organization=org) if org else qs.none()

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
            result['trend'] = _get_trend(qs, 'created_at', period, user_timezone(request.user))
        return Response(result)


class AgentReportView(APIView):
    """GET /reports/agents/ — agent performance summary. Admin + developer."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from apps.agents.models import Agent
        from apps.leads.models import Lead

        from django.db.models import Q

        agents_qs = Agent.objects.filter(is_active=True)
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            agents_qs = agents_qs.filter(organization=org) if org else Agent.objects.none()

        # Single annotated query — eliminates the N+1 of the old implementation
        agents_with_stats = agents_qs.annotate(
            lead_count=Count('assigned_leads', distinct=True),
            qualified_count=Count(
                'assigned_leads',
                filter=Q(assigned_leads__status='qualified'),
                distinct=True,
            ),
        )

        data = [{
            'id':           a.id,
            'name':         a.name,
            'phone':        a.phone,
            'is_verified':  a.is_verified,
            'total_leads':  a.lead_count,
            'closed_leads': a.qualified_count,
            'closed_deals': a.closed_deals,
            'rating':       float(a.rating),
            'primary_city': a.primary_city,
        } for a in agents_with_stats]

        data.sort(key=lambda a: a['total_leads'], reverse=True)
        return Response({'count': len(data), 'results': data})


class PropertyReportView(APIView):
    """GET /reports/properties/ — property inventory stats."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from apps.properties.models import Property

        qs = Property.objects.filter(is_active=True)
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            qs = qs.filter(organization=org) if org else qs.none()

        by_type   = dict(qs.annotate(ptype=property_type_expression()).values_list('ptype').annotate(c=Count('id')).values_list('ptype', 'c'))
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
            result['trend'] = _get_trend(qs, 'created_at', period, user_timezone(request.user))
        return Response(result)


class DealReportView(APIView):
    """GET /reports/deals/ — deal lock analytics. Admin + developer."""
    permission_classes = [IsAdminOrOrgAdmin]

    def get(self, request):
        from apps.escrow.models import EscrowDeal
        from django.db.models import ExpressionWrapper, DurationField, F

        qs = EscrowDeal.objects.all()
        if request.user.role == 'developer':
            org = get_user_org(request.user)
            qs = qs.filter(property__organization=org) if org else qs.none()

        status_counts = dict(
            qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
        )
        gateway_counts = dict(
            qs.values_list('payment_gateway').annotate(c=Count('id')).values_list('payment_gateway', 'c')
        )

        # Avg hours from initiation to payment confirmation (created_at → lock_started_at)
        confirmed_qs = qs.filter(lock_started_at__isnull=False)
        avg_hours = None
        if confirmed_qs.exists():
            delta = confirmed_qs.annotate(
                hours=ExpressionWrapper(
                    F('lock_started_at') - F('created_at'),
                    output_field=DurationField(),
                )
            ).aggregate(avg=Avg('hours'))['avg']
            if delta:
                avg_hours = round(delta.total_seconds() / 3600, 1)

        return Response({
            'total_locks': qs.count(),
            'completed':   status_counts.get('released',  0),
            'expired':     status_counts.get('expired',   0),
            'disputed':    status_counts.get('disputed',  0),
            'avg_confirm_hours': avg_hours,
            'by_status':   status_counts,
            'by_gateway':  gateway_counts,
        })


class RevenueReportView(APIView):
    """GET /reports/revenue/ — deal + payment revenue analytics. Admin only."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.escrow.models import EscrowDeal
        from apps.payments.models import Payment

        deals    = EscrowDeal.objects.all()
        payments = Payment.objects.all()

        total_token_locked = deals.filter(
            status__in=['locked', 'released']
        ).aggregate(s=Sum('token_amount'))['s'] or 0

        avg_token = deals.filter(
            status__in=['locked', 'released']
        ).aggregate(a=Avg('token_amount'))['a'] or 0

        by_gateway = dict(
            deals.values_list('payment_gateway')
                 .annotate(c=Count('id'))
                 .values_list('payment_gateway', 'c')
        )
        by_channel = dict(
            deals.values_list('initiated_via')
                 .annotate(c=Count('id'))
                 .values_list('initiated_via', 'c')
        )

        completed_payments     = payments.filter(status='completed')
        total_payments_amount  = completed_payments.aggregate(s=Sum('amount'))['s'] or 0
        payments_by_gateway    = dict(
            completed_payments.values_list('gateway')
                              .annotate(c=Count('id'))
                              .values_list('gateway', 'c')
        )

        result = {
            'deals': {
                'total':              deals.count(),
                'locked':             deals.filter(status='locked').count(),
                'released':           deals.filter(status='released').count(),
                'expired':            deals.filter(status='expired').count(),
                'cancelled':          deals.filter(status='cancelled').count(),
                'total_token_amount': total_token_locked,
                'avg_token_amount':   int(avg_token),
                'by_gateway':         by_gateway,
                'by_channel':         by_channel,
            },
            'payments': {
                'total_completed_amount': total_payments_amount,
                'by_gateway':             payments_by_gateway,
            },
        }

        period = request.query_params.get('period')
        if period in ('weekly', 'monthly'):
            result['deals']['trend']    = _get_trend(deals, 'lock_started_at', period, user_timezone(request.user))
            result['payments']['trend'] = _get_trend(completed_payments, 'created_at', period, user_timezone(request.user))

        return Response(result)


class BotReportView(APIView):
    """GET /reports/bot/ — WhatsApp bot activity analytics. Admin only."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.leads.models import UnifiedMessage
        from apps.whatsapp.models import WhatsAppSession

        now  = timezone.now()
        msgs = UnifiedMessage.objects.filter(session__isnull=False)      # T-3100b: both logs

        cutoff_30d = now - datetime.timedelta(days=30)
        cutoff_7d  = now - datetime.timedelta(days=7)

        active_30d = (
            msgs.filter(created_at__gte=cutoff_30d, direction='inbound')
                .values('session__phone').distinct().count()
        )
        active_7d = (
            msgs.filter(created_at__gte=cutoff_7d, direction='inbound')
                .values('session__phone').distinct().count()
        )

        cutoff_14d = now - datetime.timedelta(days=14)
        daily_rows = (
            msgs.filter(created_at__gte=cutoff_14d)
                .annotate(day=TruncDay('created_at', tzinfo=user_timezone(request.user)))
                .values('day')
                .annotate(count=Count('id'))
                .order_by('day')
        )
        daily_trend = [
            {'period': r['day'].strftime('%Y-%m-%d'), 'count': r['count']}
            for r in daily_rows
        ]

        by_type = dict(
            msgs.values_list('msg_type')
                .annotate(c=Count('id'))
                .values_list('msg_type', 'c')
        )

        result = {
            'total_messages':   msgs.count(),
            'inbound':          msgs.filter(direction='inbound').count(),
            'outbound':         msgs.filter(direction='outbound').count(),
            'total_sessions':   WhatsAppSession.objects.count(),
            'active_users_7d':  active_7d,
            'active_users_30d': active_30d,
            'by_message_type':  by_type,
            'daily_trend':      daily_trend,
        }

        period = request.query_params.get('period')
        if period in ('weekly', 'monthly'):
            result['trend'] = _get_trend(msgs, 'created_at', period, user_timezone(request.user))

        return Response(result)


class MonthlyReportListView(APIView):
    """GET /reports/monthly/ — list monthly org reports."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = request.user.role

        if role == 'developer':
            org = get_user_org(request.user)
            if org is None:
                return Response([], status=status.HTTP_200_OK)
            qs = MonthlyReport.objects.filter(organization=org)

        elif role == 'admin':
            qs = MonthlyReport.objects.all()
            org_id = request.query_params.get('org')
            if org_id:
                try:
                    import uuid as _uuid
                    qs = qs.filter(organization_id=_uuid.UUID(org_id))
                except (ValueError, AttributeError):
                    return Response(
                        {'detail': 'Invalid org UUID.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        else:
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        qs = qs.select_related('organization').order_by('-period_start')[:24]

        return Response([
            {
                'id':           str(r.id),
                'organization': str(r.organization_id),
                'period_start': r.period_start.isoformat(),
                'period_end':   r.period_end.isoformat(),
                'status':       r.status,
                'pdf_url':      r.pdf_url or None,
                'created_at':   r.created_at.isoformat(),
                'ready_at':     r.ready_at.isoformat() if r.ready_at else None,
            }
            for r in qs
        ])


class AgentPerformanceView(APIView):
    """
    GET /reports/agent-performance/?from=YYYY-MM-DD&to=YYYY-MM-DD[&agent=<id>][&organization=<id>]

    T-3010 (ADR-056). Managers of the active organization (owner, org admin, team or
    sales manager) see every agent; an agent sees only their own row; platform admins
    name the organization. Local dates of the organization, at most 366 days.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[OpenApiParameter('from', str, description='First local day, YYYY-MM-DD (default: 29 days before "to")'),
                    OpenApiParameter('to', str, description='Last local day, YYYY-MM-DD (default: today)'),
                    OpenApiParameter('agent', int, description='One agent (managers only)'),
                    OpenApiParameter('organization', str, description='Platform admins only')],
        responses={200: OpenApiResponse(description='Per-agent enquiries, viewings and leads, with totals'),
                   400: OpenApiResponse(description='Invalid period'), 403: OpenApiResponse(description='Not a manager or agent')})
    def get(self, request):
        from rest_framework import exceptions

        from apps.core.permissions import _SALES_MANAGER_ROLES, active_org_role
        from apps.organizations.models import Organization

        from . import performance as P
        user, q = request.user, request.query_params
        if user.role == 'admin':
            org = Organization.objects.filter(pk=q['organization']).first() if _is_uuid(q.get('organization')) else None
            if org is None:
                raise exceptions.ValidationError({'organization': 'Name the organization.'})
            manager = True
        else:
            org, role = get_user_org(user), active_org_role(user)
            manager = role in _SALES_MANAGER_ROLES
            if org is None or not (manager or getattr(user, 'agent_profile', None)):
                raise exceptions.PermissionDenied('Only the organization\'s managers and agents see this report.')
        agents = P.agents_of(org)
        if not manager:
            agents = agents.filter(user=user)
        elif q.get('agent'):
            if not str(q['agent']).isdigit():
                raise exceptions.ValidationError({'agent': 'An agent id.'})
            agents = agents.filter(pk=int(q['agent']))
        try:
            period = P.period(org, q.get('from'), q.get('to'))
        except P.PeriodInvalid as exc:
            raise exceptions.ValidationError({'period': str(exc)}) from None
        return Response(P.report(org, period, agents))


class ListingPerformanceView(APIView):
    """
    GET /reports/listing-performance/?from&to[&listing=<id>][&organization=<id>][&sort][&limit][&offset]

    T-3060 (ADR-058). Impressions, detail views, saves and enquiries per listing, with
    the rates between them. Managers of the active organization see all of its
    listings; everyone else sees the listings they own, list or are assigned to.
    Platform admins name the organization or the listing. ``listing`` adds a
    day-by-day series. Local days, at most 366.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[OpenApiParameter('from', str, description='First local day, YYYY-MM-DD (default: 29 days before "to")'),
                    OpenApiParameter('to', str, description='Last local day, YYYY-MM-DD (default: today)'),
                    OpenApiParameter('listing', str, description='One listing: adds its daily series'),
                    OpenApiParameter('organization', str, description='Platform admins only'),
                    OpenApiParameter('sort', str, enum=['impressions', 'detail_views', 'saves', 'enquiries']),
                    OpenApiParameter('limit', int, description='1–200, default 50'),
                    OpenApiParameter('offset', int)],
        responses={200: OpenApiResponse(description='Per-listing counts and rates, with totals'),
                   400: OpenApiResponse(description='Invalid parameter'),
                   404: OpenApiResponse(description='Listing not found or not yours')})
    def get(self, request):
        from rest_framework import exceptions

        from apps.core.permissions import _SALES_MANAGER_ROLES, active_org_role
        from apps.organizations.models import Organization
        from apps.properties.models import Property

        from . import listing_performance as L
        from . import performance as P
        user, q = request.user, request.query_params
        sort = q.get('sort') or 'impressions'
        if sort not in L.SORTS:
            raise exceptions.ValidationError({'sort': f'One of {", ".join(L.SORTS)}.'})
        try:
            limit = int(q.get('limit') or L.DEFAULT_LIMIT)
            offset = int(q.get('offset') or 0)
            if not 1 <= limit <= L.MAX_LIMIT or not 0 <= offset <= 100_000:
                raise ValueError
        except ValueError:
            raise exceptions.ValidationError({'limit': f'limit 1–{L.MAX_LIMIT}, offset ≥ 0.'}) from None
        listing_id = q.get('listing')
        if listing_id and not _is_uuid(listing_id):
            raise exceptions.ValidationError({'listing': 'A listing id.'})

        if user.role == 'admin':
            org = Organization.objects.filter(pk=q['organization']).first() if _is_uuid(q.get('organization')) else None
            if org is not None:
                listings = Property.objects.filter(organization=org)
            elif listing_id:
                listings = Property.objects.all()
            else:
                raise exceptions.ValidationError({'organization': 'Name the organization or the listing.'})
        else:
            org = get_user_org(user)
            listings = Property.objects.filter(L.scope_q(user, org, active_org_role(user) in _SALES_MANAGER_ROLES))
        one = None
        if listing_id:
            one = listings.filter(pk=listing_id).first()
            if one is None:
                raise exceptions.NotFound('Listing not found.')
            listings = Property.objects.filter(pk=one.pk)
            org = org or one.organization
        try:
            period = P.period(org, q.get('from'), q.get('to'))
        except P.PeriodInvalid as exc:
            raise exceptions.ValidationError({'period': str(exc)}) from None
        return Response(L.report(listings, period, sort=sort, limit=limit, offset=offset, daily_for=one))


def _is_uuid(value) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
