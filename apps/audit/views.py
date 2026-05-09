from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PropertyAudit


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


class AuditListView(APIView):
    """GET /audit/ — admin list of all property audits."""
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = PropertyAudit.objects.select_related('user').order_by('-created_at')[:100]
        return Response([{
            'id':               a.id,
            'phone':            a.phone,
            'city':             a.city,
            'location':         a.location,
            'property_type':    a.property_type,
            'area_marla':       float(a.area_marla) if a.area_marla else None,
            'estimated_value_pkr': a.estimated_value_pkr,
            'risk_score':       a.risk_score,
            'investment_grade': a.investment_grade,
            'liquidity_score':  a.liquidity_score,
            'has_pdf':          bool(a.pdf_file),
            'created_at':       a.created_at.isoformat(),
        } for a in qs])


def download_pdf(request, audit_id):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")
    audit = get_object_or_404(PropertyAudit, id=audit_id)
    if not audit.pdf_file:
        raise Http404
    return FileResponse(
        audit.pdf_file.open('rb'),
        content_type='application/pdf',
        as_attachment=True,
        filename=f"PakProp_Audit_{audit.id}.pdf",
    )
