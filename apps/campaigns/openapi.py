"""
OpenAPI descriptions of the campaign and public referral endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts or redirects; these describe
them for the schema only. Keep them in step with ``views.py`` and ``public_views.py``.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

templates = extend_schema(responses={200: inline_serializer('CampaignTemplate', {
    'name': s.CharField(), 'language': s.CharField(), 'category': s.CharField(),
    'body_preview': s.CharField(allow_blank=True), 'components': s.JSONField()}, many=True), 403: ERROR},
    description='Meta-approved WhatsApp templates for the caller\'s organization; empty without one.')
platform_stats = extend_schema(auth=[], responses=inline_serializer('PublicPlatformStats', {
    'total_verifications': s.IntegerField(), 'scams_caught': s.IntegerField(), 'active_orgs': s.IntegerField(),
    'leads_generated_today': s.IntegerField()}), description='Anonymous aggregate counts, cached for 60 seconds.')
CONVERTED = inline_serializer('ReferralConverted', {'org_whatsapp_number': s.CharField(allow_blank=True),
                                                    'org_slug': s.CharField()})
referral_convert = extend_schema(
    auth=[],
    request=inline_serializer('ReferralConvertRequest', {
        'ref': s.CharField(max_length=40), 'phone': s.CharField(help_text='E.164, e.g. +923001234567'),
        'source': s.CharField(required=False, default='web', max_length=20)}),
    responses={201: CONVERTED, 200: OpenApiResponse(CONVERTED, description='No lead could be created'),
               400: ERROR, 404: ERROR, 429: ERROR},
    description='Creates a lead in the referring organization and returns its WhatsApp number.')
verify_redirect = extend_schema(
    auth=[], parameters=[OpenApiParameter('ref', str)],
    responses={302: OpenApiResponse(description='To the public verify page (with `org` and `ref` when the code '
                                                'is valid)')},
    description='Counts the click on a referral link.')
