"""Reported enquiry threads, for moderators (T-2051, ADR-045)."""
import base64

from django.http import Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_field
from rest_framework import exceptions, serializers
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import AuditLog
from apps.enquiries import safety
from apps.enquiries.models import MessageAttachment, ThreadReport
from apps.enquiries.services import USER, EnquiryError

from .views import IsModerator

MAX_MESSAGES = 200


class ThreadReportSerializer(serializers.ModelSerializer):
    """The reporter's side, not their name."""
    thread_kind = serializers.CharField(source='enquiry.kind', read_only=True)
    subject = serializers.CharField(source='enquiry.subject', read_only=True)
    side = serializers.CharField(read_only=True, help_text='Who reported: user or responder')
    thread_status = serializers.CharField(source='enquiry.status', read_only=True)

    class Meta:
        model = ThreadReport
        fields = ['id', 'thread_kind', 'subject', 'thread_status', 'side', 'category', 'text', 'status', 'priority',
                  'due_at', 'note', 'decided_at', 'created_at']
        read_only_fields = fields


class ReportedMessageSerializer(serializers.Serializer):
    side = serializers.CharField()
    body = serializers.CharField()
    warnings = serializers.ListField(child=serializers.CharField())
    attachments = serializers.ListField(child=serializers.UUIDField())
    created_at = serializers.DateTimeField()


class ThreadReportDetailSerializer(ThreadReportSerializer):
    messages = serializers.SerializerMethodField(help_text=f'The newest {MAX_MESSAGES}')

    class Meta(ThreadReportSerializer.Meta):
        fields = [*ThreadReportSerializer.Meta.fields, 'messages']
        read_only_fields = fields

    @extend_schema_field(ReportedMessageSerializer(many=True))
    def get_messages(self, r):
        rows = r.enquiry.messages.prefetch_related(safety.attachment_meta()).order_by('-created_at', '-id')[:MAX_MESSAGES]
        return ReportedMessageSerializer([{
            'side': USER if m.direction == 'inbound' else 'responder', 'body': m.body,
            'warnings': safety.warnings(m.body), 'attachments': [a.pk for a in m.attachments.all()],
            'created_at': m.created_at} for m in rows], many=True).data


def _open_report(request, pk) -> ThreadReport:
    """The thread can be read only while its report is open, by someone not part of it, and it is logged."""
    r = get_object_or_404(ThreadReport.objects.select_related('enquiry'), pk=pk)
    if r.reporter_id == request.user.pk or safety.is_party(r.enquiry, request.user):
        raise exceptions.PermissionDenied('You are part of this thread.')
    return r


def _log_read(request, r, detail):
    AuditLog.objects.create(actor=request.user, action=AuditLog.Action.UPDATE, target_model='ThreadReport',
                            target_id=str(r.pk), detail=detail)


class ThreadReportListView(ListAPIView):
    permission_classes = [IsAuthenticated, IsModerator]
    serializer_class = ThreadReportSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return ThreadReport.objects.none()
        value = self.request.query_params.get('status', 'open')
        if value not in ThreadReport.Status.values:
            raise exceptions.ValidationError({'status': [f'One of {", ".join(ThreadReport.Status.values)}.']})
        return ThreadReport.objects.filter(status=value).select_related('enquiry')

    @extend_schema(parameters=[OpenApiParameter('status', str, enum=ThreadReport.Status.values)])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ThreadReportDetailView(APIView):
    """With the messages while the report is open. Every read is logged."""
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(responses=ThreadReportDetailSerializer)
    def get(self, request, pk):
        r = _open_report(request, pk)
        if r.status != ThreadReport.Status.OPEN:
            return Response({**ThreadReportSerializer(r).data, 'messages': []})
        _log_read(request, r, 'Read reported thread')
        return Response(ThreadReportDetailSerializer(r).data)


class ThreadReportAttachmentView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(responses={(200, 'image/*'): OpenApiTypes.BINARY})
    def get(self, request, pk, attachment_id):
        r = _open_report(request, pk)
        a = MessageAttachment.objects.filter(pk=attachment_id, message__enquiry_id=r.enquiry_id).first()
        if a is None or r.status != ThreadReport.Status.OPEN:
            raise Http404
        _log_read(request, r, 'Viewed photo in reported thread')
        return image_response(a)


def image_response(a) -> HttpResponse:
    return HttpResponse(base64.b64decode(a.payload), content_type=a.content_type, headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f'inline; filename="{a.pk}"', 'Content-Security-Policy': "default-src 'none'; sandbox"})


class ThreadDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['dismiss', 'close'])
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class ThreadReportDecisionView(APIView):
    permission_classes = [IsAuthenticated, IsModerator]

    @extend_schema(request=ThreadDecisionSerializer, responses={
        200: ThreadReportSerializer, 403: OpenApiResponse(description='Part of the thread'),
        409: OpenApiResponse(description='Already decided')})
    def post(self, request, pk):
        from apps.enquiries.views import EnquiryRefused
        get_object_or_404(ThreadReport, pk=pk)
        s = ThreadDecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            r = safety.decide(pk, request.user, **s.validated_data)
        except EnquiryError as exc:
            raise EnquiryRefused(exc) from exc
        return Response(ThreadReportSerializer(r).data)
