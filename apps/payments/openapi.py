"""
OpenAPI descriptions of the payment endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py``. Gateway callbacks (Safepay,
bSecure, Stripe) are signed server-to-server calls, not part of the client API.
"""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

webhook = extend_schema(exclude=True)

payments = extend_schema(responses={200: inline_serializer('PaymentList', {
    'count': s.IntegerField(), 'results': inline_serializer('PaymentRow', {
        'id': s.UUIDField(), 'user': s.CharField(help_text='Payer phone'), 'amount': s.DecimalField(20, 2),
        'currency': s.CharField(), 'purpose': s.CharField(), 'gateway': s.CharField(), 'status': s.CharField(),
        'reference': s.CharField(allow_blank=True), 'checkout_url': s.CharField(allow_blank=True),
        'deal_id': s.UUIDField(allow_null=True), 'created_at': s.DateTimeField()}, many=True)}), 403: ERROR},
    description='Platform admins only; the 200 newest.')
checkout = extend_schema(
    request=inline_serializer('CheckoutRequest', {'gateway': s.CharField(
        required=False, help_text='Must be the active gateway (also accepted as ?gateway=)')}),
    responses={200: inline_serializer('CheckoutSession', {
        'deal_id': s.UUIDField(), 'gateway': s.CharField(), 'checkout_url': s.URLField(), 'message': s.CharField()}),
        400: ERROR, 403: ERROR, 404: ERROR, 502: ERROR},
    description='The buyer (or an admin) starts the token payment for an initiated deal.')
sepa = extend_schema(
    request=inline_serializer('SepaPaymentRequest', {
        'iban': s.CharField(), 'account_name': s.CharField(), 'mandate_accepted': s.BooleanField()}),
    responses={201: inline_serializer('SepaPaymentStarted', {
        'deal_id': s.UUIDField(), 'payment_intent_id': s.CharField(), 'status': s.CharField(),
        'message': s.CharField()}), 400: ERROR, 403: ERROR, 404: ERROR, 502: ERROR},
    description='EU organisations only. The deal locks when the debit clears.')
payment_return = extend_schema(
    parameters=[OpenApiParameter('deal_id', str, required=True),
                OpenApiParameter('status', str, description='As the gateway redirected: success | cancelled')],
    responses={200: inline_serializer('PaymentReturn', {
        'deal_id': s.UUIDField(), 'deal_status': s.CharField(), 'return_status': s.CharField(),
        'property': s.CharField(), 'message': s.CharField()}), 400: ERROR, 403: ERROR, 404: ERROR},
    description='Display only; the gateway callback confirms the payment.')
