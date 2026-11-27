"""
OpenAPI descriptions of the billing endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py``. Gateway callbacks (Stripe,
Safepay, bSecure) are signed server-to-server calls, not part of the client API.
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')

webhook = extend_schema(exclude=True)


def _meter(name, **extra):
    return inline_serializer(name, {'used': s.IntegerField(),
                                    'limit': s.IntegerField(allow_null=True, help_text='Null: unlimited'), **extra})


usage = extend_schema(responses={200: inline_serializer('BillingUsage', {
    'plan': s.CharField(), 'usage': inline_serializer('BillingUsageMeters', {
        'agents': _meter('BillingMeterAgents', required=False),
        'inventory': _meter('BillingMeterInventory', required=False),
        'wa_tokens': _meter('BillingMeterWaTokens', period=s.CharField(help_text='YYYY-MM'), required=False)},
        help_text='Empty for platform admins')}), 403: ERROR, 404: ERROR})
checkout = extend_schema(
    request=inline_serializer('BillingCheckoutRequest', {'plan': s.CharField()}),
    responses={200: inline_serializer('BillingCheckout', {
        'checkout_url': s.URLField(allow_null=True),
        'manual': s.BooleanField(required=False, help_text='True when the plan is changed offline by an admin')}),
        400: ERROR, 403: ERROR, 404: ERROR, 502: ERROR},
    description='Organization owners only.')
portal = extend_schema(request=None, responses={200: inline_serializer('BillingPortal', {'url': s.URLField()}),
                                                400: ERROR},
                       description='A Stripe customer-portal session for the organization\'s subscription.')
invoices = extend_schema(responses=inline_serializer('BillingInvoices', {
    'invoices': inline_serializer('BillingInvoice', {
        'id': s.CharField(), 'number': s.CharField(allow_null=True), 'amount_due': s.IntegerField(),
        'amount_paid': s.IntegerField(), 'currency': s.CharField(), 'status': s.CharField(),
        'created': s.IntegerField(help_text='Unix time'), 'hosted_invoice_url': s.URLField(allow_null=True),
        'invoice_pdf': s.URLField(allow_null=True)}, many=True)}),
    description='The 20 most recent Stripe invoices; empty without a Stripe subscription. Amounts in minor units.')
PAYMENT_SETTINGS = {
    name: s.CharField(allow_blank=True, required=False)
    for name in ('gateway', 'safepay_merchant_key', 'safepay_environment', 'bsecure_client_id', 'bsecure_environment',
                 'jazzcash_number', 'easypaisa_number', 'bank_account_number', 'bank_account_name')
}
SECRETS = ('safepay_secret_key', 'bsecure_client_secret')
payment_settings = extend_schema(responses={200: inline_serializer('OrgPaymentSettings', {
    **PAYMENT_SETTINGS, **{n: s.CharField(allow_blank=True, help_text='`__configured__` when set') for n in SECRETS}}),
    403: ERROR, 404: ERROR})
payment_settings_update = extend_schema(
    request=inline_serializer('OrgPaymentSettingsUpdate', {
        **PAYMENT_SETTINGS, **{n: s.CharField(allow_blank=True, required=False,
                                              help_text='Blank keeps the stored value') for n in SECRETS}}),
    responses={200: inline_serializer('OrgPaymentSettingsSaved', {'saved': s.BooleanField()}), 403: ERROR, 404: ERROR},
    description='Unknown keys are ignored.')
