"""Tasks and follow-ups API (T-3020, ADR-057): /api/v1/leads/tasks/."""
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import exceptions, mixins, serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.agents.models import Agent
from apps.core.idempotency import IDEMPOTENCY_PARAMETER, idempotent

from . import follow_ups as F
from .models import Lead, LeadTask


class TaskSerializer(serializers.ModelSerializer):
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())
    assignee = serializers.PrimaryKeyRelatedField(queryset=Agent.objects.all(), required=False, allow_null=True)
    assignee_name = serializers.SerializerMethodField()
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = LeadTask
        fields = ['id', 'lead', 'kind', 'title', 'notes', 'due_at', 'remind_at', 'assignee', 'assignee_name',
                  'status', 'overdue', 'completed_at', 'reminded_at', 'created_at', 'updated_at']
        read_only_fields = ['id', 'completed_at', 'reminded_at', 'created_at', 'updated_at']
        extra_kwargs = {'remind_at': {'required': False}, 'notes': {'max_length': 2000}}

    def get_assignee_name(self, t) -> str | None:
        return t.assignee.display_name if t.assignee else None

    def get_overdue(self, t) -> bool:
        return t.status == LeadTask.Status.OPEN and t.due_at < timezone.now()

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError('A title is needed.')
        return value


class TaskUpdateSerializer(TaskSerializer):
    class Meta(TaskSerializer.Meta):
        read_only_fields = [*TaskSerializer.Meta.read_only_fields, 'lead']


def _refused(exc: F.TaskError):
    if exc.field == 'lead':
        return exceptions.NotFound(str(exc))
    if exc.field == 'detail':
        return exceptions.PermissionDenied(str(exc))
    return exceptions.ValidationError({exc.field: str(exc)})


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter('lead', str), OpenApiParameter('status', str, enum=list(LeadTask.Status.values)),
    OpenApiParameter('mine', bool, description='Only tasks given to me'),
    OpenApiParameter('due', str, enum=['overdue', 'today', 'upcoming'], description='Open tasks by due time')]))
class LeadTaskViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                      mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    serializer_class = TaskSerializer

    def get_serializer_class(self):
        return TaskUpdateSerializer if self.action == 'partial_update' else TaskSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return LeadTask.objects.none()
        qs, q = F.visible(self.request.user), self.request.query_params
        if q.get('lead'):
            qs = qs.filter(lead_id=q['lead']) if _uuid(q['lead']) else qs.none()
        if q.get('status') in LeadTask.Status.values:
            qs = qs.filter(status=q['status'])
        if q.get('mine') in ('1', 'true'):
            qs = qs.filter(assignee__user=self.request.user)
        if due := q.get('due'):
            from apps.markets.services import local_day_bounds, user_timezone
            now, open_ = timezone.now(), qs.filter(status=LeadTask.Status.OPEN)
            if due == 'overdue':
                qs = open_.filter(due_at__lt=now)
            elif due == 'today':
                start, end = local_day_bounds(user_timezone(self.request.user))
                qs = open_.filter(due_at__gte=start, due_at__lt=end)
            elif due == 'upcoming':
                qs = open_.filter(due_at__gte=now)
        return qs

    @extend_schema(parameters=[IDEMPOTENCY_PARAMETER])
    @idempotent
    def create(self, request, *args, **kwargs):
        s = TaskSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            task = F.create(request.user, d['lead'], title=d['title'], due_at=d['due_at'], remind_at=d.get('remind_at'),
                            notes=d.get('notes', ''), kind=d.get('kind', LeadTask.Kind.CALL), assignee=d.get('assignee'))
        except F.TaskError as exc:
            raise _refused(exc) from None
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        if not kwargs.get('partial'):
            raise exceptions.MethodNotAllowed('PUT')
        task = self.get_object()
        s = TaskUpdateSerializer(task, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        try:
            task = F.update(request.user, task, **s.validated_data)
        except F.TaskError as exc:
            raise _refused(exc) from None
        return Response(TaskSerializer(task).data)


def _uuid(value) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
