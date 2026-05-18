import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import Organization, OrganizationConfig
from .serializers import OrganizationListSerializer, OrganizationDetailSerializer
from apps.core.permissions import IsAdminUser, IsAdminOrOrgAdmin
from .services import OrgConfigService

logger = logging.getLogger(__name__)


class OrganizationListView(APIView):
    """
    GET  /api/v1/organizations/  — list all (admin) or own org (developer)
    POST /api/v1/organizations/  — create new org (admin only)
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        role = request.user.role
        if role == 'admin':
            qs = Organization.objects.select_related('admin_user').all()
        elif role == 'developer':
            qs = Organization.objects.select_related('admin_user').filter(
                admin_user=request.user
            )
        else:
            return Response(status=status.HTTP_403_FORBIDDEN)

        serializer = OrganizationListSerializer(qs, many=True)
        return Response({'count': qs.count(), 'results': serializer.data})

    def post(self, request):
        if request.user.role != 'admin':
            return Response(
                {'detail': 'Only platform admins can create organizations.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = OrganizationDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = serializer.save()
        logger.info(f"Organization created: {org.id} '{org.name}' by admin {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data, status=status.HTTP_201_CREATED)


class OrganizationDetailView(APIView):
    """
    GET    /api/v1/organizations/<id>/  — detail
    PATCH  /api/v1/organizations/<id>/  — update (admin or org admin)
    DELETE /api/v1/organizations/<id>/  — delete (admin only)
    """
    permission_classes = [IsAuthenticated, IsAdminOrOrgAdmin]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_org(self, pk, user):
        try:
            org = Organization.objects.select_related('admin_user').get(pk=pk)
        except Organization.DoesNotExist:
            return None, Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Object-level permission: admin sees all, developer sees own only
        if user.role == 'developer' and org.admin_user_id != user.id:
            return None, Response(status=status.HTTP_403_FORBIDDEN)

        return org, None

    def get(self, request, pk):
        org, err = self._get_org(pk, request.user)
        if err:
            return err
        return Response(OrganizationDetailSerializer(org).data)

    def patch(self, request, pk):
        org, err = self._get_org(pk, request.user)
        if err:
            return err

        # Only admin can change admin_user or is_verified
        restricted = {'admin_user', 'is_verified'}
        if request.user.role != 'admin' and restricted & set(request.data.keys()):
            return Response(
                {'detail': 'Only platform admins can change admin_user or is_verified.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrganizationDetailSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(f"Organization updated: {org.id} by user {request.user.id}")
        return Response(OrganizationDetailSerializer(org).data)

    def delete(self, request, pk):
        if request.user.role != 'admin':
            return Response(
                {'detail': 'Only platform admins can delete organizations.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        org, err = self._get_org(pk, request.user)
        if err:
            return err
        org_name = org.name
        org.delete()
        logger.info(f"Organization deleted: '{org_name}' by admin {request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class OrganizationMeView(APIView):
    """
    GET  /api/v1/organizations/me/  — returns the calling developer's organization
    PATCH /api/v1/organizations/me/ — update own organization
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def _get_own_org(self, user):
        try:
            return Organization.objects.get(admin_user=user), None
        except Organization.DoesNotExist:
            return None, Response(
                {'detail': 'No organization linked to your account.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)
        org, err = self._get_own_org(request.user)
        if err:
            return err
        return Response(OrganizationDetailSerializer(org).data)

    def patch(self, request, *args, **kwargs):
        if request.user.role not in ('admin', 'developer'):
            return Response(status=status.HTTP_403_FORBIDDEN)
        org, err = self._get_own_org(request.user)
        if err:
            return err

        # Non-admin developers cannot change admin_user or verification status
        restricted = {'admin_user', 'is_verified'}
        if request.user.role != 'admin' and restricted & set(request.data.keys()):
            return Response(
                {'detail': 'Only platform admins can change admin_user or is_verified.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrganizationDetailSerializer(org, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(OrganizationDetailSerializer(org).data)


class OrgConfigView(APIView):
    """
    GET  /api/v1/organizations/me/config/  — org feature flags (org overrides + platform fallback)
    PATCH /api/v1/organizations/me/config/ — set org-level feature flag overrides
    DELETE /api/v1/organizations/me/config/<key>/ — reset key to platform default
    """
    permission_classes = [IsAuthenticated]

    def _get_org(self, user):
        if user.role not in ('admin', 'developer'):
            return None, Response(status=status.HTTP_403_FORBIDDEN)
        org = getattr(user, 'owned_organization', None)
        if org is None:
            return None, Response(
                {'detail': 'No organization linked to your account.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return org, None

    def get(self, request):
        org, err = self._get_org(request.user)
        if err:
            return err
        features = OrgConfigService.get_features(org)
        overrides = set(
            OrganizationConfig.objects.filter(organization=org).values_list('key', flat=True)
        )
        return Response({
            'features': features,
            'overrides': list(overrides),
            'allowed_keys': sorted(OrganizationConfig.ALLOWED_KEYS),
        })

    def patch(self, request):
        org, err = self._get_org(request.user)
        if err:
            return err

        invalid = set(request.data.keys()) - OrganizationConfig.ALLOWED_KEYS
        if invalid:
            return Response(
                {'detail': f"Invalid keys: {sorted(invalid)}. Allowed: {sorted(OrganizationConfig.ALLOWED_KEYS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for key, raw_value in request.data.items():
            value = 'true' if str(raw_value).lower() in ('true', '1', 'yes') else 'false'
            OrgConfigService.set(org, key, value, user=request.user)

        features = OrgConfigService.get_features(org)
        return Response({'features': features})

    def delete(self, request, key):
        org, err = self._get_org(request.user)
        if err:
            return err
        if key not in OrganizationConfig.ALLOWED_KEYS:
            return Response(
                {'detail': f"'{key}' is not an overridable key."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        OrgConfigService.reset(org, key)
        return Response({'detail': f"'{key}' reset to platform default."})
