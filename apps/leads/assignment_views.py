"""
Team and assignment rules API (T-3040, FR-CRM-007, ADR-063), under ``/api/v1/leads/assignment/``.

    GET   rules/              the active organization's rules (defaults when none)   (organization staff)
    PATCH rules/              {strategy?, match_city?, match_area?, match_language?,
                               match_specialization?, only_available?, max_open_leads?}
                                                                       (owner, org admin, team manager)
    GET   team/               its agents: open leads, cap, paused, matching fields   (organization staff)
    PATCH team/<agent_id>/    {accepts_leads?, max_open_leads?}     (owner, org admin, team manager)

Platform admins pass ``organization`` (query or body). No phone numbers are shown.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import assignment as A
from . import pipeline as P
from .models import AssignmentRules
from .pipeline_views import organization as pipeline_organization

_ORG = OpenApiParameter('organization', str, description='Platform admins only')


def organization(request, *, write: bool):
    org = pipeline_organization(request, write=False)
    if request.user.role == 'admin':
        return org
    _, role = P.actor(request.user)
    if role not in P.STAFF:
        raise exceptions.PermissionDenied("Only the organization's staff see its team routing.")
    if write and role not in A.MANAGERS:
        raise exceptions.PermissionDenied("Only the owner, admins and team managers change lead routing.")
    return org


def _invalid(exc: A.AssignmentError):
    return exceptions.ValidationError({exc.field: str(exc)})


def _rules_out(org, rules):
    return {'organization': str(org.pk), **A.rules_dict(rules), 'saved': not rules._state.adding,
            'choices': {'strategy': AssignmentRules.Strategy.values, 'match': AssignmentRules.Match.values}}


class RulesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_assignment_rules_retrieve', parameters=[_ORG],
                   responses={200: OpenApiResponse(description="The organization's assignment rules")})
    def get(self, request):
        org = organization(request, write=False)
        return Response(_rules_out(org, A.rules_for(org)))

    @extend_schema(operation_id='leads_assignment_rules_update', request=OpenApiTypes.OBJECT, parameters=[_ORG],
                   responses={200: OpenApiResponse(description='The rules after the change')})
    def patch(self, request):
        org = organization(request, write=True)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            rules = A.update_rules(org, dict(data), user=request.user)
        except A.AssignmentError as exc:
            raise _invalid(exc) from None
        return Response(_rules_out(org, rules))


class TeamView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_assignment_team_list', parameters=[_ORG],
                   responses={200: OpenApiResponse(description='Agents with load, cap and routing settings')})
    def get(self, request):
        org = organization(request, write=False)
        return Response({'organization': str(org.pk), 'results': A.team(org)})


class TeamMemberView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_assignment_team_update', request=OpenApiTypes.OBJECT, parameters=[_ORG],
                   responses={200: OpenApiResponse(description="The agent's routing in this organization")})
    def patch(self, request, agent_id):
        org = organization(request, write=True)
        agent = A.org_agents(org, active_only=False).filter(pk=agent_id).first()
        if agent is None:
            raise exceptions.NotFound("Not one of this organization's agents.")
        data = request.data if isinstance(request.data, dict) else {}
        try:
            A.update_routing(org, agent, dict(data), user=request.user)
        except A.AssignmentError as exc:
            raise _invalid(exc) from None
        row = next(r for r in A.team(org) if r['agent_id'] == str(agent.pk))
        return Response(row)
