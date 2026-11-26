"""Duplicate clusters for moderators (T-4050, FR-LST-010, ADR-069): /api/v1/moderation/duplicates/."""
from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.pagination import PageNumberPagination
from apps.properties import duplicates as D
from apps.properties.models import DuplicateCluster

from .views import IsModerator


class DuplicateClusterListView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(operation_id='moderation_duplicates_list', parameters=[
        OpenApiParameter('status', str, enum=list(DuplicateCluster.Status.values)),
        OpenApiParameter('evidence', str, enum=['image', 'fingerprint'])],
        responses={200: OpenApiResponse(description='Clusters, largest first')})
    def get(self, request):
        qs = DuplicateCluster.objects.annotate(n=Count('listings')).filter(n__gte=2)
        if request.query_params.get('status') in DuplicateCluster.Status.values:
            qs = qs.filter(status=request.query_params['status'])
        if request.query_params.get('evidence') == 'image':
            qs = qs.filter(evidence__image__gt=0)
        elif request.query_params.get('evidence') == 'fingerprint':
            qs = qs.filter(evidence__fingerprint__gt=0)
        page = PageNumberPagination()
        rows = page.paginate_queryset(qs.order_by('-n', '-updated_at'), request, view=self)
        return page.get_paginated_response([D.cluster_dict(c) for c in rows])


class DuplicateClusterDetailView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(operation_id='moderation_duplicates_retrieve',
                   responses={200: OpenApiResponse(description='One cluster')})
    def get(self, request, pk):
        return Response(D.cluster_dict(get_object_or_404(DuplicateCluster, pk=pk)))


class DuplicateClusterActionView(APIView):
    """``{"action": "confirm", "canonical": <listing id>?}`` or ``{"action": "split", "listing": <id>}``."""
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(operation_id='moderation_duplicates_action', request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiResponse(description='The cluster (null when it dissolved)'),
                              400: OpenApiResponse(description='Invalid action')})
    def post(self, request, pk):
        c = get_object_or_404(DuplicateCluster, pk=pk)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            if data.get('action') == 'confirm':
                D.confirm(c, request.user, data.get('canonical'))
            elif data.get('action') == 'split' and data.get('listing'):
                D.split(c, request.user, data['listing'])
            else:
                raise exceptions.ValidationError({'action': 'confirm, or split with a listing.'})
        except (D.ClusterError, ValueError) as exc:
            raise exceptions.ValidationError({'detail': str(exc)}) from None
        c = DuplicateCluster.objects.filter(pk=pk).first()
        return Response(D.cluster_dict(c) if c else None)
