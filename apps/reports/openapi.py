"""
OpenAPI descriptions of the report endpoints (T-1091, KI-037).

The views are plain ``APIView``s that answer with hand-built dicts; these
describe them for the schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')
PERIOD = OpenApiParameter('period', str, enum=['weekly', 'monthly'],
                          description='Adds `trend`: 8 weeks or 6 months, in the viewer\'s time zone')


def counts(**kw):
    return s.DictField(child=s.IntegerField(), help_text='value → count', **kw)


def trend(name, **kw):
    return inline_serializer(name, {'period': s.CharField(), 'count': s.IntegerField()}, many=True, **kw)


REPORT = {
    'id': s.UUIDField(), 'report_type': s.CharField(), 'status': s.CharField(),
    'file_url': s.URLField(allow_null=True), 'created_at': s.DateTimeField(),
    'ready_at': s.DateTimeField(allow_null=True),
}

generate = extend_schema(
    request=inline_serializer('ReportGenerateRequest', {
        'report_type': s.CharField(), 'property_id': s.UUIDField(required=False,
                                                                 help_text='Required for property_analysis')},
        help_text='Other keys are passed to the report as its input'),
    responses={202: inline_serializer('ReportQueued', {
        'id': s.UUIDField(), 'report_type': s.CharField(), 'status': s.CharField(), 'created_at': s.DateTimeField()}),
        400: ERROR, 404: ERROR})
status_ = extend_schema(responses={200: inline_serializer('ReportState', REPORT), 403: ERROR, 404: ERROR})
download = extend_schema(responses={302: OpenApiResponse(description='Redirect to the PDF'), 403: ERROR, 404: ERROR,
                                    409: OpenApiResponse(description='Not ready yet')})
mine = extend_schema(responses=inline_serializer('ReportHistoryItem', REPORT, many=True),
                     description='The 50 most recent.')
my_stats = extend_schema(parameters=[PERIOD], responses={200: inline_serializer('AgentPersonalStats', {
    'total_leads': s.IntegerField(), 'closed_leads': s.IntegerField(), 'hot_leads': s.IntegerField(),
    'avg_score': s.FloatField(), 'by_status': counts(), 'by_source': counts(), 'total_listings': s.IntegerField(),
    'closed_deals': s.IntegerField(), 'rating': s.FloatField(), 'is_verified': s.BooleanField(),
    'avg_response_time_hours': s.FloatField(allow_null=True), 'trend': trend('AgentStatsTrend', required=False)}),
    404: ERROR})
leads = extend_schema(parameters=[PERIOD], responses=inline_serializer('LeadReport', {
    'total': s.IntegerField(), 'avg_score': s.FloatField(), 'hot_leads': s.IntegerField(), 'by_status': counts(),
    'by_intent': counts(), 'by_source': counts(), 'trend': trend('LeadReportTrend', required=False)}))
agents = extend_schema(responses=inline_serializer('AgentReport', {
    'count': s.IntegerField(), 'results': inline_serializer('AgentReportRow', {
        'id': s.IntegerField(), 'name': s.CharField(), 'phone': s.CharField(), 'is_verified': s.BooleanField(),
        'total_leads': s.IntegerField(), 'closed_leads': s.IntegerField(help_text='Leads qualified by the assistant'),
        'closed_deals': s.IntegerField(), 'rating': s.FloatField(), 'primary_city': s.CharField(allow_blank=True)},
        many=True)}))
properties = extend_schema(parameters=[PERIOD], responses=inline_serializer('PropertyReport', {
    'total': s.IntegerField(), 'avg_ai_score': s.FloatField(), 'installment_available': s.IntegerField(),
    'by_type': counts(), 'by_legal_status': counts(), 'by_risk_level': counts(), 'by_city': counts(),
    'trend': trend('PropertyReportTrend', required=False)}))
deals = extend_schema(responses=inline_serializer('DealReport', {
    'total_locks': s.IntegerField(), 'completed': s.IntegerField(), 'expired': s.IntegerField(),
    'disputed': s.IntegerField(), 'avg_confirm_hours': s.FloatField(allow_null=True), 'by_status': counts(),
    'by_gateway': counts()}))
revenue = extend_schema(parameters=[PERIOD], responses=inline_serializer('RevenueReport', {
    'deals': inline_serializer('RevenueDeals', {
        **{n: s.IntegerField() for n in ('total', 'locked', 'released', 'expired', 'cancelled', 'total_token_amount',
                                         'avg_token_amount')},
        'by_gateway': counts(), 'by_channel': counts(), 'trend': trend('RevenueDealsTrend', required=False)}),
    'payments': inline_serializer('RevenuePayments', {
        'total_completed_amount': s.IntegerField(), 'by_gateway': counts(),
        'trend': trend('RevenuePaymentsTrend', required=False)})}))
bot = extend_schema(parameters=[PERIOD], responses=inline_serializer('BotReport', {
    **{n: s.IntegerField() for n in ('total_messages', 'inbound', 'outbound', 'total_sessions', 'active_users_7d',
                                     'active_users_30d')},
    'by_message_type': counts(), 'daily_trend': trend('BotDailyTrend', help_text='Last 14 days'),
    'trend': trend('BotReportTrend', required=False)}))
monthly = extend_schema(
    parameters=[OpenApiParameter('org', str, description='Platform admins only: one organization (UUID)')],
    responses={200: inline_serializer('MonthlyReportItem', {
        'id': s.UUIDField(), 'organization': s.UUIDField(), 'period_start': s.DateField(), 'period_end': s.DateField(),
        'status': s.CharField(), 'pdf_url': s.URLField(allow_null=True), 'created_at': s.DateTimeField(),
        'ready_at': s.DateTimeField(allow_null=True)}, many=True), 400: ERROR, 403: ERROR},
    description='The 24 most recent; an organization admin sees their own.')

# ── BI analytics (bi_views.py) ───────────────────────────────────────────────

BI_ORG = OpenApiParameter('org', str, description='Required for platform admins: the organization (UUID)')
BI_REFUSED = {400: ERROR, 403: ERROR, 404: ERROR}
funnel = extend_schema(parameters=[BI_ORG], responses={200: inline_serializer('SalesFunnel', {
    'stages': inline_serializer('SalesFunnelStage', {
        'stage': s.CharField(help_text='Discover, Qualify, Verify, Connect, Negotiate, Transact'),
        'count': s.IntegerField(), 'conversion': s.FloatField(help_text='Percent of Discover')}, many=True),
    'total_leads': s.IntegerField(), 'overall_conversion': s.FloatField()}), **BI_REFUSED})
wa_tokens = extend_schema(parameters=[BI_ORG], responses={200: inline_serializer('WaTokenUsage', {
    'monthly': inline_serializer('WaTokenMonth', {'period': s.CharField(help_text='YYYY-MM'),
                                                  'tokens': s.IntegerField()}, many=True),
    'current_period': s.CharField(), 'current_month': s.IntegerField(), 'total_6m': s.IntegerField()}),
    **BI_REFUSED}, description='The last six months.')
speed_leaderboard = extend_schema(
    parameters=[BI_ORG, OpenApiParameter('top_n', int, description='1–50, default 10'),
                OpenApiParameter('refresh', str, enum=['1'], description='Recompute instead of the cached ranking')],
    responses={200: inline_serializer('AgentSpeedRow', {
        'rank': s.IntegerField(), 'agent_id': s.IntegerField(), 'name': s.CharField(),
        'avg_response_time_hours': s.FloatField(allow_null=True), 'closed_deals': s.IntegerField(),
        'rating': s.FloatField()}, many=True), **BI_REFUSED},
    description='Agents ranked by response speed.')
