from django.db.models import Avg, Count, Q, prefetch_related_objects
from django.db.models.functions import TruncMonth, TruncWeek

from apps.compliance.parsers import MaskedMultiPartParser
from apps.markets.services import user_timezone
from rest_framework import filters, status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers as drf_serializers

from apps.core.permissions import IsOwnerOrReadOnly, IsAgentOrAdmin, get_user_org
from apps.core.query_params import choice_param, int_param
from apps.core.throttles import PropertySearchThrottle, ScorePropertyThrottle
from .models import Amenity, Property, PropertyImage
from .taxonomy import property_type_expression
from .serializers import (AmenitySerializer, ListingTransitionSerializer, PriceHistorySerializer,
                          PropertyCreateSerializer, PropertyDetailSerializer,
                          PropertyImageSerializer, PropertyListSerializer,
                          TransitionRequestSerializer)


def _transition_api_error(exc):
    """lifecycle.TransitionError → DRF exception (400 fields / 403 / 409)."""
    from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
    if exc.http_status == 400:
        return ValidationError(exc.fields or {'status': str(exc)})
    if exc.http_status == 403:
        return PermissionDenied(str(exc))
    err = APIException(str(exc))
    err.status_code = 409
    return err


def _manage_q(user):
    """Listings ``user`` manages (owner or listing agent): they see them in any status."""
    if not getattr(user, 'is_authenticated', False):
        return Q(pk__in=[])
    return Q(owner=user) | Q(listed_by_agent__user=user)


def status_param(params) -> list[str] | None:
    raw = params.get('status', '').strip()
    if not raw:
        return None
    vals = [v.strip() for v in raw.split(',') if v.strip()]
    bad = [v for v in vals if v not in Property.Status.values]
    if bad or not vals:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'status': f"Unknown status: {', '.join(bad) or raw}."})
    return vals




_LIST_PARAMETERS = [
    OpenApiParameter('search', str, description=(
        'Full-text: every word must match, as a prefix (ADR-015). A full reference '
        'number (RT-LHR-2026-000000001) matches exactly.')),
    OpenApiParameter('ref_no', str, description='Exact reference number (case-insensitive).'),
    OpenApiParameter('status', str, description=(
        'Lifecycle status(es), comma-separated (T-1080). Default: public listings only '
        '(published, under_offer). Non-public statuses return only listings you manage.')),
    OpenApiParameter('city', str, description='City name, case-insensitive exact match.'),
    OpenApiParameter('type', str, enum=Property.PropertyType.values,
                     description='Deprecated (T-1070d): use category.'),
    OpenApiParameter('category', str, enum=Property.Category.values),
    OpenApiParameter('subtype', str, enum=[c[0] for c in Property.SUBTYPE_CHOICES]),
    OpenApiParameter('amenities', str, description=(
        'Comma-separated amenity keys; a listing must have all of them '
        '(at most 10).')),
    OpenApiParameter('location_id', int, description=(
        'Location id; matches it and every location under it (T-1060a).')),
    OpenApiParameter('legal_status', str, enum=Property.LegalStatus.values),
    OpenApiParameter('purpose', str, enum=Property.Purpose.values),
    OpenApiParameter('min_bedrooms', int, description='Inclusive (0-50).'),
    OpenApiParameter('min_bathrooms', int, description='Inclusive (0-50).'),
    OpenApiParameter('min_price', int, description='Inclusive, in the listing currency.'),
    OpenApiParameter('max_price', int, description='Inclusive, in the listing currency.'),
    OpenApiParameter('min_score', int, description='Minimum AI score (0-100).'),
    OpenApiParameter('near', str, description=(
        '"lat,lng": with radius_km, listings whose public point is within the radius '
        '(T-1060b). Adds distance_km; ordering=distance_km sorts by it.')),
    OpenApiParameter('radius_km', float, description='With near; default 5, max GEO_MAX_RADIUS_KM.'),
    OpenApiParameter('bbox', str, description='"min_lng,min_lat,max_lng,max_lat" (public point).'),
]


def _floats(params, name, n):
    from rest_framework.exceptions import ValidationError
    raw = params.get(name, '')
    try:
        values = [float(x) for x in raw.split(',')]
    except ValueError:
        values = []
    if len(values) != n:
        raise ValidationError({name: f'Expected {n} comma-separated numbers.'})
    return values


def apply_geo_filters(qs, params):
    """?near/radius_km and ?bbox (T-1060b); public point only (apps.geo.search)."""
    from django.conf import settings
    from rest_framework.exceptions import ValidationError
    from apps.geo.search import within_bbox, within_radius
    near = 'near' in params
    if near:
        lat, lng = _floats(params, 'near', 2)
        try:
            radius = float(params.get('radius_km') or 5)
        except ValueError:
            raise ValidationError({'radius_km': 'Must be a number.'})
        if not (0 < radius <= settings.GEO_MAX_RADIUS_KM):
            raise ValidationError({'radius_km': f'Must be in (0, {settings.GEO_MAX_RADIUS_KM:g}].'})
        try:
            qs = within_radius(qs, lat, lng, radius)
        except ValueError as exc:
            raise ValidationError({'near': str(exc)})
    elif 'distance_km' in (params.get('ordering') or ''):
        raise ValidationError({'ordering': 'distance_km needs near=lat,lng.'})
    if 'bbox' in params:
        min_lng, min_lat, max_lng, max_lat = _floats(params, 'bbox', 4)
        try:
            qs = within_bbox(qs, min_lng, min_lat, max_lng, max_lat)
        except ValueError as exc:
            raise ValidationError({'bbox': str(exc)})
    return qs


@extend_schema_view(list=extend_schema(parameters=_LIST_PARAMETERS))
class PropertyViewSet(viewsets.ModelViewSet):
    # Visibility by status is applied in get_queryset (T-1080).
    queryset = Property.objects.all()
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    filter_backends = [filters.OrderingFilter]
    throttle_classes = [PropertySearchThrottle]

    def get_permissions(self):
        if self.action == 'create':
            # Only agents, developers, and admins may list properties.
            # Clients (role=user) are WhatsApp-only and must not submit via API.
            return [IsAgentOrAdmin()]
        return super().get_permissions()
    ordering_fields = ['ai_score', 'price', 'created_at', 'distance_km']
    ordering        = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':    return PropertyListSerializer
        if self.action == 'create':  return PropertyCreateSerializer
        return PropertyDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            'owner', 'organization', 'assigned_agent', 'listed_by_agent', 'location_ref'
        ).prefetch_related('images', 'amenities')
        user = self.request.user

        # Lifecycle visibility (T-1080): the public sees published / under offer;
        # owners and listing agents also see their own listings in any status.
        from .lifecycle import is_staff
        statuses = status_param(self.request.query_params)
        staff = is_staff(user)
        if statuses:
            qs = qs.filter(status__in=statuses)
            if not staff and set(statuses) - Property.VISIBLE_STATUSES:
                qs = qs.filter(Q(is_active=True) | _manage_q(user))
        elif self.action == 'list':
            qs = qs.filter(is_active=True)
        elif self.action == 'mine':
            qs = qs.exclude(status=Property.Status.ARCHIVED)
            if not staff:
                qs = qs.filter(Q(is_active=True) | _manage_q(user))
        elif not staff:
            qs = qs.filter(Q(is_active=True) | _manage_q(user))

        # Developers see only their org's inventory
        if user.is_authenticated and user.role == 'developer':
            org = get_user_org(user)
            # Empty, but keep going: later steps add annotations (distance_km)
            # that ?ordering= may name.
            qs = qs.filter(organization=org) if org else qs.none()

        # Agents see only their org's inventory (freelance agents see all active)
        if user.is_authenticated and user.role == 'agent':
            org = get_user_org(user)
            qs = qs.filter(organization=org) if org else qs.none()

        # Consumers never see other orgs' sandbox / demo inventory (ADR-002).
        # A client's own private listings stay visible to them.
        if user.is_authenticated and user.role not in ('admin', 'developer', 'agent'):
            qs = qs.filter(Q(pk__in=Property.objects.visible_to(None).values('pk')) | _manage_q(user))

        params = self.request.query_params
        if (ref_no := params.get('ref_no', '').strip()):
            # Generated refs are upper-case: exact match uses the unique index (KI-036).
            qs = qs.filter(ref_no=ref_no.upper())
        if (city := params.get('city')):
            qs = qs.filter(city__iexact=city)
        if (ptype := choice_param(params, 'type', Property.PropertyType.values)):
            from .taxonomy import categories_for_property_type
            qs = qs.filter(category__in=categories_for_property_type(ptype))   # legacy alias
        if (category := choice_param(params, 'category', Property.Category.values)):
            qs = qs.filter(category=category)
        if (subtype := choice_param(params, 'subtype', [c[0] for c in Property.SUBTYPE_CHOICES])):
            qs = qs.filter(subtype=subtype)
        if (loc_id := int_param(params, 'location_id', min_value=1, clamp=False)) is not None:
            from apps.locations.models import Location
            loc = Location.objects.filter(pk=loc_id).only('path').first()
            if loc is None:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({'location_id': 'Unknown location.'})
            qs = qs.filter(loc.descendants_q('location_ref__path'))
        qs = apply_geo_filters(qs, params)
        if (keys := amenity_keys_param(params)):
            from .taxonomy import filter_all_amenities
            qs = filter_all_amenities(qs, keys)
        if (legal := choice_param(params, 'legal_status', Property.LegalStatus.values)):
            qs = qs.filter(legal_status=legal)
        if (purpose := choice_param(params, 'purpose', Property.Purpose.values)):
            qs = qs.filter(purpose=purpose)
        if (beds := int_param(params, 'min_bedrooms', max_value=50, clamp=False)) is not None:
            qs = qs.filter(bedrooms__gte=beds)
        if (baths := int_param(params, 'min_bathrooms', max_value=50, clamp=False)) is not None:
            qs = qs.filter(bathrooms__gte=baths)
        min_price = int_param(params, 'min_price')
        max_price = int_param(params, 'max_price')
        if min_price is not None and max_price is not None and min_price > max_price:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'min_price': 'Must not exceed max_price.'})
        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)
        if (min_score := int_param(params, 'min_score', max_value=100, clamp=False)) is not None:
            qs = qs.filter(ai_score__gte=min_score)
        if search_term := params.get('search', '').strip():
            # Index-backed (prop_search_tsv_gin); see ADR-015 and T-1070f.
            from .text_search import apply_text_search
            qs = apply_text_search(qs, search_term)
        return qs

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        listing = getattr(self, '_retrieved', None)          # set by get_object below
        if listing is not None and listing.status in Property.VISIBLE_STATUSES and listing.is_active:
            from .analytics import detail_viewed
            detail_viewed(request, listing)       # T-3060; the lister's own side is not counted
        return response

    def get_object(self):
        obj = super().get_object()
        if self.action == 'retrieve':
            self._retrieved = obj
        return obj

    def perform_create(self, serializer):
        from .models import Property as _P
        from apps.organizations.models import Organization
        user = self.request.user

        # TRIAL plan gate — max 10 active properties per org
        def _check_trial_limit(org):
            if org and org.plan == Organization.Plan.TRIAL:
                count = _P.objects.filter(organization=org).count()
                if count >= 10:
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied(
                        "Trial plan limit reached (10 properties). Upgrade to add more listings."
                    )

        if user.role == 'admin':
            extra = {}
        elif user.role == 'developer':
            from rest_framework.exceptions import PermissionDenied
            org = get_user_org(user)
            if not org:
                raise PermissionDenied("No organization found.")
            _check_trial_limit(org)
            extra = {
                'owner': user,
                'organization': org,
                'listing_owner_type': _P.ListingOwnerType.ORGANIZATION,
            }
        elif user.role == 'agent':
            from rest_framework.exceptions import PermissionDenied
            org = get_user_org(user)
            if not org:
                raise PermissionDenied("No organization found.")
            _check_trial_limit(org)
            try:
                agent_profile = user.agent_profile
            except Exception:
                agent_profile = None
            extra = {
                'organization': org,
                'listed_by_agent': agent_profile,
                'listing_owner_type': _P.ListingOwnerType.ORGANIZATION,
            }
        else:
            extra = {'owner': user, 'listing_owner_type': _P.ListingOwnerType.CLIENT}
        from .lifecycle import TransitionError, initial_status, on_create
        try:
            from apps.markets.services import default_country, market_code_for_country
            names = {f.name for f in _P._meta.concrete_fields} | {f.attname for f in _P._meta.concrete_fields}
            like = _P(**{k: v for k, v in {**serializer.validated_data, **extra}.items() if k in names},
                      **({} if serializer.validated_data.get('market') else {'market_id': market_code_for_country(
                          serializer.validated_data.get('country') or default_country())}))
            like._normalise_size_and_taxonomy()
            extra['status'] = initial_status(serializer.validated_data.pop('status', None), like, user)
        except TransitionError as exc:
            raise _transition_api_error(exc)
        from .price_history import price_change_context
        with price_change_context(user=user, source='api'):
            prop = serializer.save(**extra)
        on_create(prop, user)
        if 'location_ref' not in serializer.validated_data:
            from apps.locations.matching import link_listing
            link_listing(prop)
        try:
            from apps.properties.tasks import score_property_task
            score_property_task.delay(str(prop.id))
        except ImportError:
            pass

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def request_verification(self, request, pk=None):
        prop = self.get_object()
        is_owner = (prop.owner == request.user)
        is_org_admin = (
            request.user.role == 'developer'
            and prop.organization is not None
            and get_user_org(request.user) == prop.organization
        )
        is_agent_of_prop = (
            request.user.role == 'agent'
            and prop.listed_by_agent is not None
            and getattr(request.user, 'agent_profile', None) == prop.listed_by_agent
        )
        if not is_owner and not is_org_admin and not is_agent_of_prop and request.user.role != 'admin':
            return Response(
                {'detail': 'Not authorized to request verification for this property.'},
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
        from django.db.models import Q as _Q
        user = request.user
        base = self.get_queryset()
        if user.role == 'agent':
            try:
                profile = user.agent_profile
                qs = base.filter(
                    _Q(owner=user) | _Q(listed_by_agent=profile)
                )
            except Exception:
                qs = base.filter(owner=user)
        else:
            qs = base.filter(owner=user)
        serializer = PropertyListSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @extend_schema(request={'multipart/form-data': inline_serializer('ImageUpload', {
                       'images': drf_serializers.ListField(child=drf_serializers.ImageField()),
                       'alt_text': drf_serializers.CharField(required=False)})},
                   responses=PropertyImageSerializer(many=True))
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='upload-images', parser_classes=[MaskedMultiPartParser])
    def upload_images(self, request, pk=None):
        """
        Upload up to MAX_IMAGES_PER_LISTING images (T-1110). Each file is
        sniffed, decoded under a pixel budget and re-encoded (metadata
        stripped) before storage; one bad file rejects the whole request.
        """
        from django.conf import settings
        from django.db import transaction
        from apps.core.images import ImageRejected, sanitize_image
        from .lifecycle import can_manage
        prop = self.get_object()
        if not can_manage(request.user, prop):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        files = request.FILES.getlist('images')
        if not files:
            return Response({'detail': 'No images provided.'}, status=status.HTTP_400_BAD_REQUEST)
        limit = settings.MAX_IMAGES_PER_LISTING
        if len(files) > limit:
            return Response({'detail': f'Max {limit} images per listing.'}, status=status.HTTP_400_BAD_REQUEST)
        cleaned, errors = [], {}
        for i, f in enumerate(files):
            try:
                cleaned.append(sanitize_image(f, thumbnail_side=settings.IMAGE_THUMB_SIDE))
            except ImageRejected as exc:
                errors[f'images[{i}]'] = str(exc)             # never echo the client file name
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        if len({c.sha256 for c in cleaned}) != len(cleaned):
            return Response({'detail': 'The same image was sent twice.'}, status=status.HTTP_400_BAD_REQUEST)
        alt = (request.data.get('alt_text') or '')[:250]
        with transaction.atomic():
            Property.objects.select_for_update().filter(pk=prop.pk).first()     # serialise uploads
            existing = list(prop.images.values_list('sha256', flat=True))
            if len(existing) + len(cleaned) > limit:
                return Response({'detail': f'Max {limit} images per listing. {len(existing)} already uploaded.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if set(existing) & {c.sha256 for c in cleaned}:
                return Response({'detail': 'This image is already on the listing.'},
                                status=status.HTTP_400_BAD_REQUEST)
            created = [PropertyImage.objects.create(
                property=prop, image=c.content, thumbnail=c.thumbnail, order=len(existing) + i,
                alt_text=alt, width=c.width, height=c.height, size_bytes=c.size, sha256=c.sha256,
                uploaded_by=request.user) for i, c in enumerate(cleaned)]
        serializer = PropertyImageSerializer(created, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=['PATCH'], request=PropertyImageSerializer, responses=PropertyImageSerializer)
    @extend_schema(methods=['DELETE'], responses={204: None})
    @action(detail=True, methods=['patch', 'delete'], permission_classes=[IsAuthenticated],
            url_path=r'images/(?P<image_id>[0-9a-f-]+)')
    def delete_image(self, request, pk=None, image_id=None):
        """PATCH caption / alt_text; DELETE removes the image and its thumbnail."""
        from .lifecycle import can_manage
        prop = self.get_object()
        if not can_manage(request.user, prop):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            img = prop.images.get(id=image_id)
        except (PropertyImage.DoesNotExist, ValueError):
            return Response({'detail': 'Image not found.'}, status=status.HTTP_404_NOT_FOUND)
        if request.method == 'PATCH':
            ser = PropertyImageSerializer(img, data=request.data, partial=True, context={'request': request})
            ser.is_valid(raise_exception=True)
            ser.save()
            return Response(ser.data)
        if img.thumbnail:
            img.thumbnail.delete(save=False)
        img.image.delete(save=False)
        img.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=inline_serializer('ImageOrder', {
                       'ids': drf_serializers.ListField(child=drf_serializers.UUIDField())}),
                   responses=PropertyImageSerializer(many=True))
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated],
            url_path='images/reorder')
    def reorder_images(self, request, pk=None):
        """POST {ids: [...]}: the listing's image ids in the new order (all of them)."""
        from .lifecycle import can_manage
        prop = self.get_object()
        if not can_manage(request.user, prop):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        ids = [str(i) for i in (request.data.get('ids') or [])]
        current = {str(i) for i in prop.images.values_list('id', flat=True)}
        if len(ids) != len(set(ids)) or set(ids) != current:
            return Response({'ids': 'Give every image id of this listing exactly once.'},
                            status=status.HTTP_400_BAD_REQUEST)
        for order, image_id in enumerate(ids):
            PropertyImage.objects.filter(pk=image_id, property=prop).update(order=order)
        fresh = PropertyImage.objects.filter(property=prop).order_by('order', 'created_at')
        return Response(PropertyImageSerializer(fresh, many=True, context={'request': request}).data)

    @action(detail=False, methods=['get'], url_path='recommended')
    def recommended(self, request):
        """GET /properties/recommended/?lead=<uuid> — top matching properties for a lead."""
        lead_id = request.query_params.get('lead')
        if not lead_id:
            return Response({'detail': 'lead param required.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.leads.models import Lead
        from django.shortcuts import get_object_or_404
        from apps.properties.recommendations import recommend_for_lead

        if not (request.user.is_staff or request.user.role in ('admin', 'developer', 'agent')):
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        lead = get_object_or_404(Lead, id=lead_id)

        # Org scoping: developers and agents can only access their own org's leads
        user = request.user
        if user.role in ('developer', 'agent'):
            org = get_user_org(user)
            if not org:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            if lead.organization_id and str(lead.organization_id) != str(org.id):
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        props = recommend_for_lead(lead)
        prefetch_related_objects(props, 'amenities')
        return Response(PropertyListSerializer(props, many=True, context={'request': request}).data)

    def perform_update(self, serializer):
        from .price_history import price_change_context
        from django.db import transaction
        from .lifecycle import TransitionError, transition
        before = (serializer.instance.city, serializer.instance.location, serializer.instance.market_id)
        want_active = serializer.validated_data.pop('is_active', None)
        with transaction.atomic(), price_change_context(user=self.request.user, source='api'):
            prop = serializer.save()
            if want_active is not None and want_active != prop.is_active:
                # Legacy toggle → audited transition (published / archived).
                to = Property.Status.PUBLISHED if want_active else Property.Status.ARCHIVED
                try:
                    transition(prop, to, actor=self.request.user, reason='is_active via API')
                except TransitionError as exc:
                    raise _transition_api_error(exc)
            from apps.moderation.services import after_edit
            after_edit(prop, self.request.user)
        if 'location_ref' not in serializer.validated_data and \
                (prop.city, prop.location, prop.market_id) != before:
            from apps.locations.matching import link_listing
            link_listing(prop, force=True)        # the text moved: re-match

    @extend_schema(methods=['GET'], responses=inline_serializer('ListingLifecycle', {
                       'status': drf_serializers.CharField(),
                       'allowed': drf_serializers.ListField(child=drf_serializers.CharField()),
                       'history': ListingTransitionSerializer(many=True)}))
    @extend_schema(methods=['POST'], request=TransitionRequestSerializer,
                   responses=PropertyDetailSerializer)
    @action(detail=True, methods=['get', 'post'], url_path='transitions')
    def transitions(self, request, pk=None):
        """
        GET: current status, the targets you may move to, and the audit trail
        (listers and staff only). POST {to, reason}: change status — 409 on an
        invalid edge, 403 if not yours / moderator-only, 400 if incomplete.
        """
        from .lifecycle import TransitionError, allowed_targets, can_manage, transition
        prop = self.get_object()
        if not can_manage(request.user, prop):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You cannot manage this listing.')
        if request.method == 'GET':
            return Response({'status': prop.status,
                             'allowed': allowed_targets(prop, request.user),
                             'history': ListingTransitionSerializer(
                                 prop.transitions.select_related('actor')[:100], many=True).data})
        body = TransitionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            transition(prop, body.validated_data['to'], actor=request.user,
                       reason=body.validated_data['reason'])
        except TransitionError as exc:
            raise _transition_api_error(exc)
        return Response(PropertyDetailSerializer(prop, context=self.get_serializer_context()).data)

    @extend_schema(responses=PriceHistorySerializer(many=True))
    @action(detail=True, methods=['get'], url_path='price-history')
    def price_history(self, request, pk=None):
        """GET /properties/{id}/price-history/ — newest first; same visibility as the listing."""
        prop = self.get_object()
        qs = prop.price_history.all()
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(PriceHistorySerializer(page, many=True).data)
        return Response(PriceHistorySerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='similar')
    def similar(self, request, pk=None):
        """GET /properties/{id}/similar/ — active listings similar to this property."""
        from apps.properties.recommendations import similar_to
        prop = self.get_object()
        props = similar_to(prop)
        prefetch_related_objects(props, 'amenities')
        return Response(PropertyListSerializer(props, many=True, context={'request': request}).data)


MAX_AMENITY_FILTERS = 10


def amenity_keys_param(params, name='amenities') -> list[str]:
    """Parse `?amenities=gym,pool`; unknown or inactive keys → 400."""
    from . import taxonomy
    keys = taxonomy.parse_amenity_keys(params.get(name, ''))
    if not keys:
        return []
    from rest_framework.exceptions import ValidationError
    if len(keys) > MAX_AMENITY_FILTERS:
        raise ValidationError({name: f'At most {MAX_AMENITY_FILTERS} keys.'})
    known = taxonomy.active_amenity_keys(keys)
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise ValidationError({name: f"Unknown amenity: {', '.join(unknown)}."})
    return keys


class PropertyTaxonomyView(APIView):
    """
    GET /properties/taxonomy/?market=PK&lang=ur
    Listing categories with their subtypes, and the active amenities
    (limited to those available in ``market`` when given).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter('market', str, description='Two-letter market code.'),
            OpenApiParameter('lang', str, description='Label language (falls back to en).'),
        ],
        responses=inline_serializer('ListingTaxonomy', {
            'categories': drf_serializers.ListField(child=inline_serializer('ListingCategory', {
                'key': drf_serializers.CharField(),
                'label': drf_serializers.CharField(),
                'subtypes': drf_serializers.ListField(child=inline_serializer('ListingSubtype', {
                    'key': drf_serializers.CharField(), 'label': drf_serializers.CharField()})),
            })),
            'amenities': AmenitySerializer(many=True),
        }),
    )
    def get(self, request):
        from . import taxonomy
        market = (request.query_params.get('market') or '').strip().upper() or None
        if market and (len(market) != 2 or not market.isalpha()):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'market': 'Must be a two-letter market code.'})
        lang = (request.query_params.get('lang') or 'en').strip().lower()[:10]
        amenities = [a for a in Amenity.objects.filter(is_active=True) if a.available_in(market)]
        return Response({
            'categories': [
                {'key': c, 'label': taxonomy.CATEGORY_LABELS[c],
                 'subtypes': [{'key': s, 'label': taxonomy.subtype_label(s)}
                              for s in taxonomy.SUBTYPES[c]]}
                for c in taxonomy.CATEGORIES
            ],
            'amenities': AmenitySerializer(amenities, many=True, context={'lang': lang}).data,
        })


class PropertyCompareView(APIView):
    """
    GET /properties/compare/?ids=uuid1,uuid2,uuid3
    Returns full detail for up to 4 properties side-by-side.
    """
    permission_classes = [IsAgentOrAdmin]

    def get(self, request):
        ids_raw = request.query_params.get('ids', '')
        ids = [i.strip() for i in ids_raw.split(',') if i.strip()]
        if not ids:
            return Response({'error': "Provide 'ids' as comma-separated UUIDs."}, status=400)
        if len(ids) > 4:
            return Response({'error': 'Maximum 4 properties can be compared at once.'}, status=400)
        import uuid
        try:
            ids = [str(uuid.UUID(i)) for i in ids]
        except ValueError:
            return Response({'error': "'ids' must be valid UUIDs."}, status=400)
        props = Property.objects.filter(id__in=ids, is_active=True)
        user = request.user
        if user.role == 'developer':
            org = get_user_org(user)
            if not org:
                props = Property.objects.none()
            else:
                props = props.filter(organization=org)
        elif user.role == 'agent':
            org = get_user_org(user)
            props = props.filter(organization=org) if org else props.filter(
                Q(pk__in=Property.objects.visible_to(None).values('pk')) | _manage_q(user))
        return Response({
            'count': props.count(),
            'results': PropertyDetailSerializer(props, many=True, context={'request': request}).data,
        })


class PropertyMarketTrendsView(APIView):
    """
    GET /properties/market-trends/?city=Lahore&period=monthly|weekly
    Returns average price and listing count per city per time period.
    """
    permission_classes = [IsAgentOrAdmin]

    def get(self, request):
        city   = request.query_params.get('city', '').strip()
        period = request.query_params.get('period', 'monthly')

        qs = Property.objects.filter(is_active=True, price__isnull=False)
        user = request.user
        if user.role == 'developer':
            org = get_user_org(user)
            if not org:
                return Response({'results': []})
            qs = qs.filter(organization=org)
        elif user.role == 'agent':
            org = get_user_org(user)
            qs = qs.filter(organization=org) if org else qs.filter(
                Q(pk__in=Property.objects.visible_to(None).values('pk')) | _manage_q(user))
        if city:
            qs = qs.filter(city__icontains=city)

        trunc_fn = TruncMonth if period == 'monthly' else TruncWeek

        data = list(
            qs
            .annotate(period=trunc_fn('created_at', tzinfo=user_timezone(user)))
            .annotate(property_type=property_type_expression())
            .values('period', 'city', 'property_type')
            .annotate(avg_price=Avg('price'), count=Count('id'))
            .order_by('city', 'period')
        )

        # Serialize datetime fields to ISO strings
        for row in data:
            if row.get('period'):
                row['period'] = row['period'].date().isoformat()

        return Response({'results': data})