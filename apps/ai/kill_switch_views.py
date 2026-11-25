"""Kill switch API (T-2070): platform AI and operations admins stop and restart the assistant."""
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.generics import ListCreateAPIView, get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAIAdmin, IsOpsAdmin
from apps.core.query_params import choice_param
from apps.markets.models import Market
from apps.organizations.models import Organization

from . import kill_switch
from .models import AIKillSwitch


class KillSwitchSerializer(serializers.ModelSerializer):
    market = serializers.PrimaryKeyRelatedField(queryset=Market.objects.all(), required=False, allow_null=True)
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all(), required=False,
                                                      allow_null=True)

    class Meta:
        model = AIKillSwitch
        fields = ['id', 'scope', 'market', 'organization', 'reason', 'created_by', 'created_at',
                  'released_by', 'released_at']
        read_only_fields = ['id', 'created_by', 'created_at', 'released_by', 'released_at']

    def validate(self, data):
        S = AIKillSwitch.Scope
        for name, kind in (('market', S.MARKET), ('organization', S.ORGANIZATION)):
            if (data.get(name) is not None) != (data['scope'] == kind):
                raise serializers.ValidationError({name: f'Required for scope {kind}, not allowed otherwise.'})
        return data


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'kill_switch_conflict'


class KillSwitchListView(ListCreateAPIView):
    """Stops in force (``?active=false``: the history as well); POST stops the assistant."""
    permission_classes = [IsAIAdmin | IsOpsAdmin]
    serializer_class = KillSwitchSerializer

    def get_queryset(self):
        qs = AIKillSwitch.objects.all()
        if getattr(self, 'swagger_fake_view', False):
            return qs.none()
        if choice_param(self.request.query_params, 'active', ('true', 'false'), 'true') == 'false':
            return qs
        return qs.filter(released_at__isnull=True)

    @extend_schema(parameters=[OpenApiParameter('active', str, enum=['true', 'false'])])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(responses={201: KillSwitchSerializer, 409: OpenApiResponse(description='Already stopped')})
    def post(self, request, *args, **kwargs):
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        try:
            switch = kill_switch.stop(reason=v['reason'], market=v.get('market'), organization=v.get('organization'),
                                      actor=request.user, request=request)
        except kill_switch.AlreadyStopped as exc:
            raise Conflict(str(exc)) from exc
        return Response(self.get_serializer(switch).data, status=status.HTTP_201_CREATED)


class KillSwitchReleaseView(APIView):
    """Restart the assistant: ends this stop. Other stops that cover the same users stay in force."""
    permission_classes = [IsAIAdmin | IsOpsAdmin]

    @extend_schema(request=None, responses={200: KillSwitchSerializer,
                                            409: OpenApiResponse(description='Already released')})
    def post(self, request, pk):
        try:
            switch = kill_switch.release(get_object_or_404(AIKillSwitch, pk=pk), actor=request.user, request=request)
        except kill_switch.AlreadyReleased as exc:
            raise Conflict(str(exc)) from exc
        return Response(KillSwitchSerializer(switch).data)
