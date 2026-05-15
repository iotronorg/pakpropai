from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncMonth, TruncWeek
from rest_framework import filters, status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from apps.core.permissions import IsOwnerOrReadOnly, IsAgentOrAdmin
from apps.core.throttles import PropertySearchThrottle, ScorePropertyThrottle
from .models import Property, PropertyImage
from .serializers import (PropertyCreateSerializer, PropertyDetailSerializer,
                          PropertyImageSerializer, PropertyListSerializer)

_ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
_MAX_IMAGE_SIZE      = 5 * 1024 * 1024   # 5 MB
_MAX_IMAGES_PER_PROP = 10


class PropertyViewSet(viewsets.ModelViewSet):
    queryset = Property.objects.filter(is_active=True)
    permission_classes = [IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    throttle_classes = [PropertySearchThrottle]

    def get_permissions(self):
        if self.action == 'create':
            # Only agents, developers, and admins may list properties.
            # Clients (role=user) are WhatsApp-only and must not submit via API.
            return [IsAgentOrAdmin()]
        return super().get_permissions()
    search_fields   = ['ref_no', 'title', 'city', 'location', 'description']
    ordering_fields = ['ai_score', 'price_pkr', 'created_at']
    ordering        = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':    return PropertyListSerializer
        if self.action == 'create':  return PropertyCreateSerializer
        return PropertyDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        if (ref_no := params.get('ref_no')):
            qs = qs.filter(ref_no__iexact=ref_no)
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
        if self.request.user.role == 'admin':
            # Admin explicitly selects owner; may be None for anonymous listings
            extra = {}
        else:
            # Non-admin always owns their own listing
            extra = {'owner': self.request.user}
        prop = serializer.save(**extra)
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except ImportError:
            pass

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def request_verification(self, request, pk=None):
        prop = self.get_object()
        if prop.owner != request.user and request.user.role != 'admin':
            return Response(
                {'detail': 'Only the property owner can request verification.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from apps.verification.models import Verification
        v = Verification.objects.create(
            property=prop,
            requested_by=request.user,
            status=Verification.Status.PENDING,
        )
        return Response(
            {'verification_id': str(v.id), 'status': v.status},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='rescore', throttle_classes=[ScorePropertyThrottle])
    def rescore(self, request, pk=None):
        if request.user.role != 'admin':
            return Response({'error': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        prop = self.get_object()
        from apps.properties.tasks import score_property_task
        score_property_task.delay(str(prop.id))
        return Response({'queued': True, 'property_id': str(prop.id)})

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='rescore-all', throttle_classes=[ScorePropertyThrottle])
    def rescore_all(self, request):
        if request.user.role != 'admin':
            return Response({'error': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        from apps.properties.tasks import rescore_all_properties_task
        rescore_all_properties_task.delay()
        return Response({'queued': True})

    @action(detail=False, methods=['get'])
    def mine(self, request):
        qs = self.get_queryset().filter(owner=request.user)
        serializer = PropertyListSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='upload-images', parser_classes=[MultiPartParser])
    def upload_images(self, request, pk=None):
        prop = self.get_object()

        if prop.owner != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        files = request.FILES.getlist('images')
        if not files:
            return Response({'detail': 'No images provided.'}, status=status.HTTP_400_BAD_REQUEST)

        existing = prop.images.count()
        if existing + len(files) > _MAX_IMAGES_PER_PROP:
            return Response(
                {'detail': f'Max {_MAX_IMAGES_PER_PROP} images per property. {existing} already uploaded.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for f in files:
            if f.content_type not in _ALLOWED_IMAGE_TYPES:
                return Response(
                    {'detail': f'{f.name}: unsupported type. Use JPEG, PNG, or WebP.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if f.size > _MAX_IMAGE_SIZE:
                return Response(
                    {'detail': f'{f.name}: exceeds 5 MB limit.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        created = [
            PropertyImage.objects.create(
                property=prop,
                image=f,
                order=existing + i,
                uploaded_by=request.user,
            )
            for i, f in enumerate(files)
        ]

        serializer = PropertyImageSerializer(created, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], permission_classes=[IsAuthenticated],
            url_path=r'images/(?P<image_id>[0-9a-f-]+)')
    def delete_image(self, request, pk=None, image_id=None):
        prop = self.get_object()

        if prop.owner != request.user and request.user.role != 'admin':
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            img = prop.images.get(id=image_id)
        except PropertyImage.DoesNotExist:
            return Response({'detail': 'Image not found.'}, status=status.HTTP_404_NOT_FOUND)

        img.image.delete(save=False)
        img.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyCompareView(APIView):
    """
    GET /properties/compare/?ids=uuid1,uuid2,uuid3
    Returns full detail for up to 4 properties side-by-side.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ids_raw = request.query_params.get('ids', '')
        ids = [i.strip() for i in ids_raw.split(',') if i.strip()]
        if not ids:
            return Response({'error': "Provide 'ids' as comma-separated UUIDs."}, status=400)
        if len(ids) > 4:
            return Response({'error': 'Maximum 4 properties can be compared at once.'}, status=400)
        props = Property.objects.filter(id__in=ids, is_active=True)
        return Response({
            'count': props.count(),
            'results': PropertyDetailSerializer(props, many=True, context={'request': request}).data,
        })


class PropertyMarketTrendsView(APIView):
    """
    GET /properties/market-trends/?city=Lahore&period=monthly|weekly
    Returns average price and listing count per city per time period.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        city   = request.query_params.get('city', '').strip()
        period = request.query_params.get('period', 'monthly')

        qs = Property.objects.filter(is_active=True, price_pkr__isnull=False)
        if city:
            qs = qs.filter(city__icontains=city)

        trunc_fn = TruncMonth if period == 'monthly' else TruncWeek

        data = list(
            qs
            .annotate(period=trunc_fn('created_at'))
            .values('period', 'city', 'property_type')
            .annotate(avg_price_pkr=Avg('price_pkr'), count=Count('id'))
            .order_by('city', 'period')
        )

        # Serialize datetime fields to ISO strings
        for row in data:
            if row.get('period'):
                row['period'] = row['period'].date().isoformat()

        return Response({'results': data})