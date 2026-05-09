from django.db.models import Q
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from apps.core.permissions import IsOwnerOrReadOnly
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