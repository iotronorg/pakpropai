"""
Reading the unified audit trail (T-3070, ADR-060).

    GET /api/v1/audit-events/?category&action&actor&organization&target_type&target_id&from&to&limit&offset
    GET /api/v1/audit-events/verify/        (platform admins)

Platform admins see every event. The owner and admins of an organization see
that organization's events, without security events and without addresses or
browser strings.
"""
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_tz

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import CATEGORIES
from .permissions import IsAdminUser

_ORG_ROLES = ('owner', 'org_admin')
_HIDDEN_FOR_ORGS = ('ip', 'user_agent')


def _day(value, name):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise exceptions.ValidationError({name: 'YYYY-MM-DD.'}) from None


class AuditEventListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[OpenApiParameter('category', str, enum=list(CATEGORIES)), OpenApiParameter('action', str),
                    OpenApiParameter('actor', str), OpenApiParameter('organization', str, description='Admins only'),
                    OpenApiParameter('target_type', str), OpenApiParameter('target_id', str),
                    OpenApiParameter('from', str, description='First UTC day'),
                    OpenApiParameter('to', str, description='Last UTC day'),
                    OpenApiParameter('limit', int), OpenApiParameter('offset', int)],
        responses={200: OpenApiResponse(description='Events, newest first'),
                   403: OpenApiResponse(description='Not an admin of the platform or the organization')})
    def get(self, request):
        from .models import AuditEvent
        from .permissions import active_org_role, get_user_org
        from .query_params import limit_offset
        user, q = request.user, request.query_params
        qs = AuditEvent.objects.all()
        platform = user.role == 'admin'
        if platform:
            if q.get('organization'):
                qs = qs.filter(organization_id=q['organization'][:64])
        else:
            org = get_user_org(user)
            if org is None or active_org_role(user) not in _ORG_ROLES:
                raise exceptions.PermissionDenied("Only the organization's owner and admins see its audit trail.")
            qs = qs.filter(organization_id=str(org.pk)).exclude(category='security')
        if (category := q.get('category')):
            if category not in CATEGORIES:
                raise exceptions.ValidationError({'category': f'One of {", ".join(CATEGORIES)}.'})
            qs = qs.filter(category=category)
        for param, column in (('action', 'action'), ('actor', 'actor_id'), ('target_type', 'target_type'),
                              ('target_id', 'target_id')):
            if (value := q.get(param)):
                qs = qs.filter(**{column: value[:100]})
        if q.get('from'):
            qs = qs.filter(at__gte=datetime.combine(_day(q['from'], 'from'), time.min, tzinfo=dt_tz.utc))
        if q.get('to'):
            qs = qs.filter(at__lt=datetime.combine(_day(q['to'], 'to') + timedelta(days=1), time.min,
                                                   tzinfo=dt_tz.utc))
        limit, offset = limit_offset(q, default=50, maximum=200)
        total = qs.count()
        rows = []
        for e in qs.order_by('-seq')[offset:offset + limit]:
            personal = dict(e.personal or {})
            if not platform:
                for key in _HIDDEN_FOR_ORGS:
                    personal.pop(key, None)
            rows.append({'seq': e.seq, 'at': e.at, 'category': e.category, 'action': e.action,
                         'actor_id': e.actor_id or None, 'organization_id': e.organization_id or None,
                         'target_type': e.target_type, 'target_id': e.target_id, 'meta': e.meta,
                         'details': personal, 'redacted': e.redacted_at is not None, 'hash': e.record_hash})
        return Response({'count': total, 'limit': limit, 'offset': offset, 'results': rows})


class AuditChainVerifyView(APIView):
    permission_classes = [IsAdminUser]

    @extend_schema(responses={200: OpenApiResponse(description='ok, rows checked, head, first broken row')})
    def get(self, request):
        from .audit import verify
        return Response(verify().as_dict())
