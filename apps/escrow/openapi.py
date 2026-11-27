"""
OpenAPI descriptions of the deal-lock endpoints (T-1091, KI-037).

Plain ``APIView``s answering with hand-built dicts; these describe them for the
schema only. Keep them in step with ``views.py``.
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as s

from .serializers import ConfirmPaymentSerializer, EscrowDealSerializer, InitiateDealLockSerializer

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')


def _result(name, with_status=False):
    fields = {'detail': s.CharField(), 'id': s.UUIDField()}
    if with_status:
        fields['status'] = s.CharField()
    return inline_serializer(name, fields)


NOTES = inline_serializer('DealLockNotes', {'admin_notes': s.CharField(required=False)})

initiate = extend_schema(
    request=InitiateDealLockSerializer,
    responses={201: inline_serializer('DealLockInitiated', {
        'id': s.UUIDField(), 'status': s.CharField(), 'token_amount': s.IntegerField(), 'currency': s.CharField(),
        'payment_gateway': s.CharField(), 'available_payment_methods': s.ListField(child=s.CharField()),
        'payment_message': s.CharField(), 'message': s.CharField()}),
        400: ERROR,
        403: OpenApiResponse(description='Deal lock disabled (`feature_disabled`), or blocked by AML screening '
                                         '(`detail`, `screening_id`)')},
    description='Frozen for the MVP unless `feature_deal_lock` is on (MVP_SCOPE §4.2).')
confirm = extend_schema(request=ConfirmPaymentSerializer,
                        responses={200: EscrowDealSerializer, 400: ERROR, 403: ERROR, 404: ERROR},
                        description='Admins or the property\'s organization: payment received; starts the 48h lock.')
seller_confirm = extend_schema(
    request=inline_serializer('DealLockSellerConfirmRequest', {
        'token': s.CharField(required=False, help_text='The code sent to the seller; not needed for admins')}),
    responses={200: inline_serializer('DealLockSellerConfirmed', {'detail': s.CharField(),
                                                                  'id': s.UUIDField(required=False)}),
               400: ERROR, 403: ERROR, 404: ERROR})
release = extend_schema(request=NOTES, responses={200: _result('DealLockReleased', with_status=True), 400: ERROR,
                                                  403: ERROR, 404: ERROR},
                        description='Only locked deals.')
cancel = extend_schema(
    request=inline_serializer('DealLockCancelRequest', {'reason': s.CharField(required=False)}),
    responses={200: _result('DealLockCancelled'), 400: ERROR, 403: ERROR, 404: ERROR},
    description='The buyer, an admin, or the property\'s organization.')
dispute = extend_schema(request=NOTES, responses={200: _result('DealLockDisputed', with_status=True), 400: ERROR, 404: ERROR},
                        description='Platform admins only.')
