from django.db.models import Q
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from apps.core.permissions import IsOwnerOrReadOnly
from .models import Property
from .serializers import (PropertyCreateSerializer, PropertyDetailSerializer,
                          PropertyListSerializer)


class PropertyViewSet(viewsets.ModelViewSet):
    queryset = Property.objects.filter(is_active=True)
    permission_classes = [IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['title', 'city', 'location', 'description']
    ordering_fields = ['ai_score', 'price_pkr', 'created_at']
    ordering        = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':    return PropertyListSerializer
        if self.action == 'create':  return PropertyCreateSerializer
        return PropertyDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        if (city := params.get('city')):
            qs = qs.filter(city__iexact=city)
        if (ptype := params.get('type')):
            qs = qs.filter(property_type=ptype)
        if (legal := params.get('legal_status')):
            qs = qs.filter(legal_status=legal)
        if (min_price := params.get('min_price')):
            qs = qs.filter(price_pkr__gte=int(min_price))
        if (max_price := params.get('max_price')):
            qs = qs.filter(price_pkr__lte=int(max_price))
        if (min_score := params.get('min_score')):
            qs = qs.filter(ai_score__gte=int(min_score))
        return qs

    def perform_create(self, serializer):
        prop = serializer.save(owner=self.request.user)
        # Phase 7: kick off async scoring
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except ImportError:
            pass

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def request_verification(self, request, pk=None):
        prop = self.get_object()
        from apps.verification.models import Verification
        v = Verification.objects.create(
            property=prop,
            requested_by=request.user,
            status=Verification.Status.PENDING,
        )
        # Phase 7 will run OCR async
        return Response(
            {'verification_id': str(v.id), 'status': v.status},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='rescore')
    def rescore(self, request, pk=None):
        if request.user.role != 'admin':
            return Response({'error': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        prop = self.get_object()
        from apps.properties.tasks import score_property_task
        score_property_task.delay(str(prop.id))
        return Response({'queued': True, 'property_id': str(prop.id)})

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='rescore-all')
    def rescore_all(self, request):
        if request.user.role != 'admin':
            return Response({'error': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        from apps.properties.tasks import rescore_all_properties_task
        rescore_all_properties_task.delay()
        return Response({'queued': True})

    @action(detail=False, methods=['get'])
    def mine(self, request):
        qs = self.get_queryset().filter(owner=request.user)
        serializer = PropertyListSerializer(qs, many=True)
        return Response(serializer.data)