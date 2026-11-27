"""OpenAPI descriptions of the AI usage endpoints (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description="Refused; `detail` and `error_info` (ADR-017)")

ORG = OpenApiParameter('org', str, description='Platform admins: the organization (UUID)')

token_budget = extend_schema(parameters=[ORG], responses={200: inline_serializer('TokenBudgetStatus', {
    'used': s.IntegerField(), 'limit': s.IntegerField(), 'percent': s.FloatField(),
    'state': s.CharField(help_text='ok | warning | throttled | hard_limit')}), 400: ERROR, 404: ERROR},
    description='The organization\'s sliding-window token budget; `org` is required for platform admins.')
token_usage = extend_schema(
    parameters=[ORG, OpenApiParameter('start', OpenApiTypes.DATETIME), OpenApiParameter('end', OpenApiTypes.DATETIME),
                OpenApiParameter('intent', str), OpenApiParameter('model', str)],
    responses={200: inline_serializer('TokenUsageStats', {
        'total_tokens_in': s.IntegerField(), 'total_tokens_out': s.IntegerField(), 'total_calls': s.IntegerField(),
        'cache_hits': s.IntegerField(), 'cache_hit_rate': s.FloatField(help_text='Percent'),
        'estimated_usd_cost': s.FloatField(allow_null=True, help_text='Null when a model has no price'),
        'estimated_savings_usd': s.FloatField(allow_null=True)}), 404: ERROR},
    description='Platform admins: the whole platform unless `org` is given.')
