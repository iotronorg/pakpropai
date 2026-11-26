"""
Listing assistant API (T-3050, FR-LAI-001…003, ADR-059).

    GET  /api/v1/properties/{id}/ai/missing-info/
    POST /api/v1/properties/{id}/ai/describe/   {"notes": "..."}
    POST /api/v1/properties/{id}/ai/extract/    {"text": "..."}   (default: the listing's description)

Only the people who manage the listing (owner, listing agent, platform staff).
Nothing is saved: the answers are drafts and suggestions the person saves with
``PATCH /api/v1/properties/{id}/``.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.core.permissions import FeatureEnabled

from .lifecycle import can_manage
from .models import Property

FLAG = 'feature_listing_assistant'


class ListingAssistantThrottle(UserRateThrottle):
    scope = 'listing_assistant'          # each call is a paid model call


class _Base(APIView):
    permission_classes = [IsAuthenticated]

    def listing(self, request, pk) -> Property:
        p = (Property.objects.select_related('organization', 'listed_by_agent', 'market')
             .prefetch_related('amenities').filter(pk=pk).first())
        if p is None or not can_manage(request.user, p):
            raise exceptions.NotFound('Listing not found.')
        return p


def _unavailable(exc):
    from apps.ai.llm import AIStopped
    stopped = isinstance(exc, AIStopped)
    return Response({'detail': 'The assistant is paused.' if stopped else 'The assistant is unavailable; try later.',
                     'code': 'ai.stopped' if stopped else 'ai.unavailable'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE)


class MissingInfoView(_Base):
    @extend_schema(responses={200: OpenApiResponse(description='Missing fields by severity'),
                              404: OpenApiResponse(description='Not a listing you manage')})
    def get(self, request, pk):
        from apps.ai.listing_assistant import missing_info
        return Response(missing_info(self.listing(request, pk)))


class _ModelView(_Base):
    permission_classes = [IsAuthenticated, FeatureEnabled(FLAG)]
    throttle_classes = [ListingAssistantThrottle]

    def run(self, request, pk, fn, key):
        from apps.ai import listing_assistant as LA
        from apps.ai.llm import LLMUnavailable
        p = self.listing(request, pk)
        if not isinstance(request.data, dict):
            raise exceptions.ValidationError({'detail': 'Send a JSON object.'})
        try:
            return Response(fn(p, request.data.get(key), user=request.user))
        except LA.AssistantInputError as exc:
            raise exceptions.ValidationError(exc.errors) from None
        except LLMUnavailable as exc:
            return _unavailable(exc)


class DescribeView(_ModelView):
    @extend_schema(request=OpenApiTypes.OBJECT, responses={
        200: OpenApiResponse(description='Title and description drafts; sentences without a source are marked'),
        400: OpenApiResponse(description='Invalid notes'), 403: OpenApiResponse(description='Feature off'),
        404: OpenApiResponse(description='Not a listing you manage'),
        503: OpenApiResponse(description='No model available or the assistant is paused')})
    def post(self, request, pk):
        from apps.ai.listing_assistant import describe
        return self.run(request, pk, lambda p, notes, user: describe(p, notes or '', user=user), 'notes')


class ExtractView(_ModelView):
    @extend_schema(request=OpenApiTypes.OBJECT, responses={
        200: OpenApiResponse(description='Field suggestions with the quoted text; a ready PATCH body'),
        400: OpenApiResponse(description='Invalid text'), 403: OpenApiResponse(description='Feature off'),
        404: OpenApiResponse(description='Not a listing you manage'),
        503: OpenApiResponse(description='No model available or the assistant is paused')})
    def post(self, request, pk):
        from apps.ai.listing_assistant import extract
        return self.run(request, pk, extract, 'text')
