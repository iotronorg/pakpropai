"""
Pipeline stages API (T-3030, ADR-062), under ``/api/v1/leads/pipeline/``.

    GET  stages/                     the active organization's stages with lead counts (members)
    POST stages/                     add a stage {name, kind?, after?}            (owner, org admin)
    PATCH stages/<id>/               rename or change kind {name?, kind?}         (owner, org admin)
    POST stages/reorder/             {order: [ids]}                               (owner, org admin)
    POST stages/<id>/archive/        {move_to}                                    (owner, org admin)
    GET  board/?per_stage=           stages with their first leads (what the caller may see)
    POST /api/v1/leads/<id>/move-stage/ {stage, note?}   staff, or the lead's agent

Platform admins pass ``organization`` (query or body).
"""
import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.query_params import int_param

from . import pipeline as P
from .models import PipelineStage

_ORG = OpenApiParameter('organization', str, description='Platform admins only')


class PipelineConflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'pipeline.conflict'


def refused(exc: P.PipelineError):
    if exc.field == 'detail':
        return PipelineConflict({'detail': str(exc), 'code': exc.code})
    return exceptions.ValidationError({exc.field: str(exc)})


def organization(request, *, write: bool):
    from apps.organizations.models import Organization
    user = request.user
    if user.role == 'admin':
        raw = request.query_params.get('organization') or (
            request.data.get('organization') if isinstance(request.data, dict) else None)
        try:
            org = Organization.objects.filter(pk=uuid.UUID(str(raw))).first() if raw else None
        except ValueError:
            org = None
        if org is None:
            raise exceptions.ValidationError({'organization': 'An organization id.'})
        return org
    org, role = P.actor(user)
    if org is None:
        raise exceptions.PermissionDenied('Not acting for an organization.')
    if write and role not in P.ADMINS:
        raise exceptions.PermissionDenied("Only the organization's owner and admins change the pipeline.")
    return org


def _stage(org, pk) -> PipelineStage:
    stage = PipelineStage.objects.filter(organization=org, pk=pk).select_related('organization').first()
    if stage is None:
        raise exceptions.NotFound('Stage not found.')
    return stage


def _out(stage, count=None):
    return {**P.stage_dict(stage), 'position': stage.position, 'archived': stage.archived_at is not None,
            **({'count': count} if count is not None else {})}


def visible_leads(request):
    from .views import LeadViewSet
    view = LeadViewSet()
    view.request, view.action, view.format_kwarg, view.kwargs = request, 'list', None, {}
    return view.get_queryset()


class StageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_pipeline_stages_list', parameters=[_ORG, OpenApiParameter('archived', bool)],
                   responses={200: OpenApiResponse(description='Stages in order with the lead count of each')})
    def get(self, request):
        org = organization(request, write=False)
        totals = P.counts(visible_leads(request), org)
        rows = P.stages(org, archived=request.query_params.get('archived') in ('1', 'true'))
        return Response({'organization': str(org.pk),
                         'results': [_out(s, totals.get(s.pk, 0) if s.archived_at is None else 0) for s in rows]})

    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiResponse(description='The new stage'),
                                                           409: OpenApiResponse(description='Too many stages')})
    def post(self, request):
        org = organization(request, write=True)
        d = request.data if isinstance(request.data, dict) else {}
        try:
            stage = P.add(org, name=d.get('name'), kind=d.get('kind') or PipelineStage.Kind.OPEN,
                          after=d.get('after'), user=request.user)
        except P.PipelineError as exc:
            raise refused(exc) from None
        return Response(_out(stage), status=status.HTTP_201_CREATED)


class StageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=OpenApiTypes.OBJECT, parameters=[_ORG],
                   responses={200: OpenApiResponse(description='The stage'),
                              409: OpenApiResponse(description='Last open stage, or leads in it')})
    def patch(self, request, pk):
        org = organization(request, write=True)
        d = request.data if isinstance(request.data, dict) else {}
        try:
            stage = P.update(_stage(org, pk), name=d.get('name'), kind=d.get('kind'), user=request.user)
        except P.PipelineError as exc:
            raise refused(exc) from None
        return Response(_out(stage))


class StageReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiResponse(description='Stages in new order')})
    def post(self, request):
        org = organization(request, write=True)
        order = request.data.get('order') if isinstance(request.data, dict) else None
        if not isinstance(order, list):
            raise exceptions.ValidationError({'order': 'A list of stage ids.'})
        try:
            rows = P.reorder(org, order, user=request.user)
        except P.PipelineError as exc:
            raise refused(exc) from None
        return Response({'results': [_out(s) for s in rows]})


class StageArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiResponse(description='Leads moved'),
                                                           409: OpenApiResponse(description='Last open stage')})
    def post(self, request, pk):
        org = organization(request, write=True)
        d = request.data if isinstance(request.data, dict) else {}
        try:
            moved = P.archive(_stage(org, pk), move_to=d.get('move_to'), user=request.user)
        except P.PipelineError as exc:
            raise refused(exc) from None
        return Response({'archived': str(pk), 'moved': moved})


class BoardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[_ORG, OpenApiParameter('per_stage', int, description='1-50, default 20')],
                   responses={200: OpenApiResponse(description='Stages with count and first leads')})
    def get(self, request):
        org = organization(request, write=False)
        per = int_param(request.query_params, 'per_stage', default=20, min_value=1, max_value=50)
        return Response({'organization': str(org.pk),
                         'stages': P.board(visible_leads(request), org, per_stage=per)})


def move_stage(view, request, pk):
    """Body of ``LeadViewSet.move_stage`` (kept here with the rest of the pipeline API)."""
    from .serializers import LeadSerializer
    lead = view.get_object()
    if not P.may_move(request.user, lead):
        raise exceptions.PermissionDenied('Only the organization\'s staff or the lead\'s agent move it.')
    d = request.data if isinstance(request.data, dict) else {}
    if not d.get('stage'):
        raise exceptions.ValidationError({'stage': 'A stage id.'})
    try:
        lead = P.move(lead, d['stage'], user=request.user, note=d.get('note') or '')
    except P.PipelineError as exc:
        raise refused(exc) from None
    return Response(LeadSerializer(view.get_queryset().get(pk=lead.pk)).data)

