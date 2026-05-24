"""
External Developer API — key management + tenant-scoped data endpoints.

Authentication: ApiKeyAuthentication (Bearer rtk_… or X-API-Key header)
Authorization:  HasApiKeyScope + cross-org object isolation

Key management endpoints use the standard JWT auth (developer role).
Data endpoints use ApiKeyAuthentication exclusively.
"""
import logging

from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

security_logger = logging.getLogger('security.api_keys')


# ── DRF Authentication ─────────────────────────────────────────────────────────

class ApiKeyAuthentication(BaseAuthentication):
    """
    DRF auth class for external API tokens.

    Accepts:
      Authorization: Bearer rtk_<prefix>_<secret>
      X-API-Key: rtk_<prefix>_<secret>

    On success returns (org.admin_user, api_key_instance).
    Returns None (defers to next authenticator) when no rtk_ token is present.
    Raises AuthenticationFailed for malformed or invalid tokens.
    """

    def authenticate(self, request):
        raw_token = self._extract(request)
        if raw_token is None:
            return None

        from apps.organizations.api_keys import ApiKeyManager
        api_key = ApiKeyManager.authenticate(raw_token)

        if api_key is None:
            raise AuthenticationFailed(
                'Invalid or expired API key.',
                code='invalid_api_key',
            )

        if not api_key.organization.is_active:
            raise AuthenticationFailed(
                'Organization account is inactive.',
                code='org_inactive',
            )

        return (api_key.organization.admin_user, api_key)

    def authenticate_header(self, request):
        return 'Bearer realm="RealTron External API"'

    @staticmethod
    def _extract(request) -> str | None:
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer rtk_'):
            return auth_header[7:]
        x_api_key = request.META.get('HTTP_X_API_KEY', '')
        if x_api_key.startswith('rtk_'):
            return x_api_key
        return None


# ── DRF Permission ─────────────────────────────────────────────────────────────

class HasApiKeyScope(BasePermission):
    """
    Two-phase permission:
      1. Scope check   — the API key must declare the required scope
      2. Object check  — the object must belong to the API key's organization

    Set `required_scope` on the view. Example:
        class ExternalLeadsView(APIView):
            required_scope = 'leads:read'

    Cross-org violations are logged as high-severity security events.
    """

    def has_permission(self, request, view) -> bool:
        from apps.organizations.models import DeveloperApiKey
        api_key = request.auth
        if not isinstance(api_key, DeveloperApiKey):
            return False

        required = getattr(view, 'required_scope', None)
        if required and required not in api_key.scopes:
            security_logger.warning(
                'API_KEY_SCOPE_DENIED: key_id=%s org=%s required=%s granted=%s path=%s',
                api_key.pk, api_key.organization_id, required, api_key.scopes, request.path,
            )
            return False
        return True

    def has_object_permission(self, request, view, obj) -> bool:
        from apps.organizations.models import DeveloperApiKey
        api_key = request.auth
        if not isinstance(api_key, DeveloperApiKey):
            return False

        obj_org = getattr(obj, 'organization', None) or getattr(obj, 'org', None)
        if obj_org is None:
            return True  # object carries no org FK — trust view-level scoping

        obj_org_id = str(obj_org.pk if hasattr(obj_org, 'pk') else obj_org)
        key_org_id = str(api_key.organization_id)

        if obj_org_id != key_org_id:
            security_logger.warning(
                'CROSS_ORG_ACCESS_ATTEMPT: key_id=%s auth_org=%s target_org=%s '
                'obj_type=%s obj_pk=%s path=%s method=%s',
                api_key.pk,
                key_org_id,
                obj_org_id,
                type(obj).__name__,
                getattr(obj, 'pk', '?'),
                request.path,
                request.method,
            )
            return False
        return True


# ── Throttle ───────────────────────────────────────────────────────────────────

class ApiKeyThrottle(SimpleRateThrottle):
    scope = 'api_key'

    def get_cache_key(self, request, view):
        from apps.organizations.models import DeveloperApiKey
        if isinstance(request.auth, DeveloperApiKey):
            return f'throttle_api_key_{request.auth.pk}'
        return None


# ── Serializers ────────────────────────────────────────────────────────────────

class ApiKeyCreateSerializer(serializers.Serializer):
    name   = serializers.CharField(max_length=100)
    scopes = serializers.ListField(
        child=serializers.CharField(), min_length=1, max_length=10
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_scopes(self, value):
        from apps.organizations.models import DeveloperApiKey
        invalid = set(value) - DeveloperApiKey.VALID_SCOPES
        if invalid:
            raise serializers.ValidationError(
                f"Unknown scopes: {sorted(invalid)}. "
                f"Valid: {sorted(DeveloperApiKey.VALID_SCOPES)}"
            )
        return value


class ApiKeyListSerializer(serializers.Serializer):
    id           = serializers.UUIDField()
    name         = serializers.CharField()
    key_prefix   = serializers.SerializerMethodField()
    scopes       = serializers.ListField(child=serializers.CharField())
    is_active    = serializers.BooleanField()
    created_at   = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)
    expires_at   = serializers.DateTimeField(allow_null=True)

    def get_key_prefix(self, obj):
        return f"rtk_{obj.key_prefix}_{'*' * 20}"  # safe display format


# ── Key Management Views (JWT auth — developer role) ───────────────────────────

class DeveloperApiKeyListCreateView(APIView):
    """
    GET  /api/v1/external/keys/   — list this org's API keys
    POST /api/v1/external/keys/   — create a new API key (raw token returned once)
    """
    permission_classes = [IsAuthenticated]

    def _get_org(self, user):
        role = getattr(user, 'role', None)
        if role == 'developer':
            return getattr(user, 'owned_organization', None)
        if role == 'admin':
            return None  # admins can't own an org; deny
        return None

    def get(self, request):
        org = self._get_org(request.user)
        if org is None:
            return Response({'detail': 'No organization found for this account.'}, status=403)
        from apps.organizations.models import DeveloperApiKey
        keys = DeveloperApiKey.objects.filter(organization=org).order_by('-created_at')
        return Response(ApiKeyListSerializer(keys, many=True).data)

    def post(self, request):
        org = self._get_org(request.user)
        if org is None:
            return Response({'detail': 'No organization found for this account.'}, status=403)

        ser = ApiKeyCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        from apps.organizations.api_keys import ApiKeyManager
        raw_token, instance = ApiKeyManager.generate(
            organization=org,
            name=ser.validated_data['name'],
            scopes=ser.validated_data['scopes'],
            expires_at=ser.validated_data.get('expires_at'),
        )

        return Response({
            'id':        str(instance.pk),
            'name':      instance.name,
            'token':     raw_token,       # shown exactly once
            'key_hint':  f"rtk_{instance.key_prefix}_{'*' * 20}",
            'scopes':    instance.scopes,
            'expires_at': instance.expires_at,
            'warning':   'Store this token securely. It will not be shown again.',
        }, status=status.HTTP_201_CREATED)


class DeveloperApiKeyRevokeView(APIView):
    """
    DELETE /api/v1/external/keys/<key_id>/ — soft-revoke an API key
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, key_id):
        from apps.organizations.models import DeveloperApiKey
        role = getattr(request.user, 'role', None)
        if role not in ('developer', 'admin'):
            return Response(status=status.HTTP_403_FORBIDDEN)

        qs = DeveloperApiKey.objects.filter(pk=key_id, is_active=True)
        if role == 'developer':
            org = getattr(request.user, 'owned_organization', None)
            if not org:
                return Response(status=status.HTTP_403_FORBIDDEN)
            qs = qs.filter(organization=org)

        key = qs.first()
        if not key:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        key.is_active = False
        key.save(update_fields=['is_active'])

        security_logger.info(
            'API_KEY_REVOKED: key_id=%s org=%s revoked_by=%s',
            key.pk, key.organization_id, request.user.pk,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── External Data Views (API key auth) ─────────────────────────────────────────

class ExternalLeadsView(APIView):
    """
    GET  /api/v1/external/leads/       — list org's qualified leads
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes     = [HasApiKeyScope]
    throttle_classes       = [ApiKeyThrottle]
    required_scope         = 'leads:read'

    def get(self, request):
        from apps.leads.models import Lead
        from apps.leads.serializers import LeadSerializer

        api_key = request.auth
        leads = Lead.objects.filter(
            organization=api_key.organization
        ).select_related('agent').order_by('-created_at')[:100]

        return Response(LeadSerializer(leads, many=True).data)


class ExternalLeadDetailView(APIView):
    """
    GET /api/v1/external/leads/<lead_id>/ — retrieve one lead (cross-org isolation enforced)
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes     = [HasApiKeyScope]
    throttle_classes       = [ApiKeyThrottle]
    required_scope         = 'leads:read'

    def get(self, request, lead_id):
        from apps.leads.models import Lead
        from apps.leads.serializers import LeadSerializer

        try:
            lead = Lead.objects.select_related('organization').get(pk=lead_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        self.check_object_permissions(request, lead)
        return Response(LeadSerializer(lead).data)


class ExternalInventoryView(APIView):
    """
    GET  /api/v1/external/inventory/   — list org's property inventory
    POST /api/v1/external/inventory/   — push a new listing into the org's inventory
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes     = [HasApiKeyScope]
    throttle_classes       = [ApiKeyThrottle]

    def get_required_scope(self):
        return 'inventory:write' if self.request.method == 'POST' else 'inventory:read'

    @property
    def required_scope(self):
        return self.get_required_scope()

    def get(self, request):
        from apps.properties.models import Property
        from apps.properties.serializers import PropertySerializer

        api_key = request.auth
        props = Property.objects.filter(
            organization=api_key.organization, is_active=True
        ).order_by('-created_at')[:200]

        return Response(PropertySerializer(props, many=True).data)

    def post(self, request):
        from apps.properties.serializers import PropertyCreateSerializer

        api_key = request.auth
        ser = PropertyCreateSerializer(
            data=request.data,
            context={'request': request, 'organization': api_key.organization},
        )
        ser.is_valid(raise_exception=True)
        prop = ser.save(organization=api_key.organization)
        return Response({'id': str(prop.pk)}, status=status.HTTP_201_CREATED)
