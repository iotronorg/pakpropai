"""
OpenAPI descriptions of the hand-built listing endpoints (T-1091, KI-037).

Describes the ``APIView``s in ``views.py`` that answer without a declared
serializer; for the schema only. Keep them in step with the views.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')


def compare(view_method):
    from .serializers import PropertyDetailSerializer
    return extend_schema(
        operation_id='properties_compare',
        parameters=[OpenApiParameter('ids', str, required=True, description='Up to 4 listing UUIDs, comma-separated')],
        responses={200: inline_serializer('PropertyComparison', {
            'count': s.IntegerField(), 'results': PropertyDetailSerializer(many=True)}), 400: ERROR, 403: ERROR},
        description='Agents and organization staff; only listings the caller may see.')(view_method)


market_trends = extend_schema(
    parameters=[OpenApiParameter('city', str, description='Contains, case-insensitive'),
                OpenApiParameter('period', str, enum=['monthly', 'weekly'])],
    responses={200: inline_serializer('MarketTrends', {'results': inline_serializer('MarketTrendRow', {
        'period': s.DateField(help_text='First day of the period, in the caller\'s time zone'),
        'city': s.CharField(), 'property_type': s.CharField(allow_null=True),
        'avg_price': s.FloatField(), 'count': s.IntegerField()}, many=True)}), 403: ERROR},
    description='Average asking price and number of listings per city, type and period.')
