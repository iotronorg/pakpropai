"""
Contacts API (T-3100, FR-CRM-001, ADR-064), under ``/api/v1/leads/contacts/``.

    GET   contacts/?search=&tag=&limit=&offset=   people among the caller's leads, newest activity first
    GET   contacts/<user_id>/                     the person: their leads here, tags, name
    PATCH contacts/<user_id>/                     {name?, tags?}                 (organization staff)
    GET   contacts/<user_id>/messages/?before=&limit=    CRM and WhatsApp messages, newest first
    GET   /api/v1/leads/<id>/messages/?before=&limit=    the same for one lead

The caller's leads decide which people they may open (an agent: the people whose
leads are assigned to them). Platform admins pass ``organization``. A person who
withdrew the consent to share contact details is listed without a phone number.
"""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.query_params import int_param, limit_offset

from . import contacts as C
from . import pipeline as P
from .pipeline_views import organization, visible_leads

_ORG = OpenApiParameter('organization', str, description='Platform admins only')
_PAGE = [OpenApiParameter('before', str, description='The `next` cursor of the previous page'),
         OpenApiParameter('limit', int, description='1-100, default 50')]
DASHBOARD = ('admin', 'agent', 'developer')


def _scope(request):
    if request.user.role not in DASHBOARD:
        raise exceptions.PermissionDenied('Dashboard access required.')
    org = organization(request, write=False)
    return org, visible_leads(request)


def _person(visible, org, user_id):
    if not C.person_leads(visible, org, user_id).exists():
        raise exceptions.NotFound('Contact not found.')
    return get_user_model().objects.get(pk=user_id)


def messages_response(request, qs):
    limit = int_param(request.query_params, 'limit', 50, min_value=1, max_value=100)
    try:
        rows, nxt = C.page(qs, before=request.query_params.get('before') or None, limit=limit)
    except C.ContactError as exc:
        raise exceptions.ValidationError({exc.field: str(exc)}) from None
    return Response({'results': C.message_dicts(rows), 'next': nxt})


class ContactListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_contacts_list',
                   parameters=[_ORG, OpenApiParameter('search', str), OpenApiParameter('tag', str),
                               OpenApiParameter('limit', int), OpenApiParameter('offset', int)],
                   responses={200: OpenApiResponse(description='People with lead counts, newest activity first')})
    def get(self, request):
        org, visible = _scope(request)
        limit, offset = limit_offset(request.query_params, default=50, maximum=200)
        rows = C.people(visible, org, search=(request.query_params.get('search') or '')[:100],
                        tag=(request.query_params.get('tag') or '')[:C.MAX_TAG])
        total = rows.count()
        return Response({'organization': str(org.pk), 'count': total,
                         'results': C.people_dicts(org, list(rows[offset:offset + limit]))})


class ContactDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_contacts_retrieve', parameters=[_ORG],
                   responses={200: OpenApiResponse(description='The person with their leads here')})
    def get(self, request, user_id):
        org, visible = _scope(request)
        return Response(C.detail(visible, org, _person(visible, org, user_id)))

    @extend_schema(operation_id='leads_contacts_update', request=OpenApiTypes.OBJECT, parameters=[_ORG],
                   responses={200: OpenApiResponse(description='The person after the change')})
    def patch(self, request, user_id):
        org, visible = _scope(request)
        if request.user.role != 'admin' and P.actor(request.user)[1] not in P.STAFF:
            raise exceptions.PermissionDenied("Only the organization's staff name and tag contacts.")
        user = _person(visible, org, user_id)
        try:
            C.annotate(org, user, dict(request.data) if isinstance(request.data, dict) else {}, actor=request.user)
        except C.ContactError as exc:
            raise exceptions.ValidationError({exc.field: str(exc)}) from None
        return Response(C.detail(visible, org, user))


class ContactMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id='leads_contacts_messages', parameters=[_ORG, *_PAGE],
                   responses={200: OpenApiResponse(description='Messages newest first, with a `next` cursor')})
    def get(self, request, user_id):
        org, visible = _scope(request)
        user = _person(visible, org, user_id)
        return messages_response(request, C.contact_messages(org, user, C.person_leads(visible, org, user_id)))
