from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from .models import PropertyAudit


def download_pdf(request, audit_id):
    audit = get_object_or_404(PropertyAudit, id=audit_id)
    if not audit.pdf_file:
        raise Http404
    return FileResponse(
        audit.pdf_file.open('rb'),
        content_type='application/pdf',
        as_attachment=True,
        filename=f"PakProp_Audit_{audit.id}.pdf",
    )
