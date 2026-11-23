from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.core.query_params import choice_param, int_param
from .models import Location
from .serializers import LocationSerializer
from .services import normalise


class LocationListView(generics.ListAPIView):
    """
    GET /api/v1/locations/?market=PK&parent=<id>&level=city&q=dha
    Browse or autocomplete the hierarchy. Unverified (review-queue) nodes are
    hidden unless ``include_unverified=1`` and the caller is staff.
    """
    serializer_class   = LocationSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[
        OpenApiParameter('market', str, description='Two-letter market code'),
        OpenApiParameter('parent', int, description='Direct children of this location'),
        OpenApiParameter('level', str, enum=Location.Level.values),
        OpenApiParameter('q', str, description='Name/alias prefix (normalised), at least 2 characters'),
        OpenApiParameter('include_unverified', bool, description='Staff only'),
    ])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        p = self.request.query_params
        qs = Location.objects.filter(is_active=True)
        if not (p.get('include_unverified') in ('1', 'true') and self.request.user.is_staff):
            qs = qs.filter(is_verified=True)
        if (market := (p.get('market') or '').strip().upper()):
            if len(market) != 2 or not market.isalpha():
                raise ValidationError({'market': 'Must be a two-letter market code.'})
            qs = qs.filter(market_id=market)
        if (parent := int_param(p, 'parent', min_value=1, clamp=False)) is not None:
            qs = qs.filter(parent_id=parent)
        if (level := choice_param(p, 'level', Location.Level.values)):
            qs = qs.filter(level=level)
        if (q := (p.get('q') or '').strip()):
            key = normalise(q)
            if len(key) < 2:
                raise ValidationError({'q': 'At least 2 characters.'})
            qs = qs.filter(match_key__startswith=key)
        return qs.order_by('path')
