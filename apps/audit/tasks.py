"""
Async Celery tasks for property audit PDF generation and WhatsApp delivery.

Chain: generate_audit_pdf_task → send_audit_via_whatsapp_task
"""
import logging
import urllib.request as _req

from celery import shared_task

from apps.audit.models import AuditDeliveryFailure, PropertyAudit

LOGO_MAX_BYTES = 2 * 1024 * 1024
REPORT_MAX_BYTES = 20 * 1024 * 1024


def _read_logo(org) -> bytes | None:
    """The org logo through the storage backend (local disk or Cloudinary), capped; None if unreadable."""
    if not org.logo:
        return None
    try:
        with org.logo.open('rb') as fh:
            data = fh.read(LOGO_MAX_BYTES + 1)
    except Exception as exc:
        logger.warning('audit pdf: logo unreadable for org %s: %s', org.pk, exc.__class__.__name__)
        return None
    return data if len(data) <= LOGO_MAX_BYTES else None


def _download_report(url: str) -> bytes:
    """The stored report PDF. Only https: the URL is ours (Cloudinary), never file:// or plain http."""
    if not (url or '').startswith('https://'):
        raise ValueError('report URL is not https')
    resp = _req.urlopen(url, timeout=20)  # nosec B310 - https only, checked above
    try:
        data = resp.read(REPORT_MAX_BYTES + 1)
    finally:
        resp.close()
    if len(data) > REPORT_MAX_BYTES:
        raise ValueError('report too large')
    return data

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0, queue='high-resource')
def generate_audit_pdf_task(self, audit_id: int, org_id: str) -> None:
    """
    1. Score the property via AuditEngine.
    2. Generate branded PDF bytes.
    3. Upload to Cloudinary.
    4. Chain send_audit_via_whatsapp_task.
    """
    from apps.audit.gateway import AuditMediaGateway
    from apps.audit.pdf import OrgBrandContext, generate_audit_pdf_bytes
    from apps.audit.services import AuditEngine
    from apps.organizations.models import Organization

    try:
        audit = PropertyAudit.objects.get(pk=audit_id)
        org = Organization.objects.get(pk=org_id)
    except (PropertyAudit.DoesNotExist, Organization.DoesNotExist) as exc:
        logger.error("generate_audit_pdf_task: object not found — %s", exc)
        return

    audit.delivery_status = 'generating'
    audit.save(update_fields=['delivery_status'])

    try:
        report = AuditEngine.run(
            city=audit.city,
            location=audit.location,
            property_type=audit.property_type,
            estimated_value=int(audit.estimated_value),
            value_currency=audit.currency,
            area_sqm=float(audit.area_size) if audit.area_size else None,
            owner_name=audit.owner_name,
            description=audit.description,
            phone=audit.phone,
        )

        brand = OrgBrandContext(
            org_name=org.name,
            brand_color=org.brand_color or '#1B4F72',
            logo_bytes=_read_logo(org),
            measurement_system=org.measurement_system,
        )

        pdf_bytes = generate_audit_pdf_bytes(report, brand_context=brand)

        cloudinary_url = AuditMediaGateway.upload_to_cloudinary(
            pdf_bytes, audit_id=audit.pk, org_id=str(org.pk)
        )

        audit.audit_data = report
        audit.cloudinary_url = cloudinary_url
        audit.delivery_status = 'ready'
        audit.save(update_fields=['audit_data', 'cloudinary_url', 'delivery_status'])

        send_audit_via_whatsapp_task.delay(audit_id, audit.phone, str(org.pk))

    except Exception as exc:
        logger.exception("generate_audit_pdf_task failed audit_id=%s: %s", audit_id, exc)
        AuditDeliveryFailure.objects.create(
            audit=audit, failure_type='pdf_gen', error_detail=str(exc)
        )
        audit.delivery_status = 'failed'
        audit.save(update_fields=['delivery_status'])


@shared_task(bind=True, max_retries=0, queue='high-resource')
def send_audit_via_whatsapp_task(self, audit_id: int, phone: str, org_id: str) -> None:
    """
    Upload PDF binary to Meta and dispatch via WhatsApp.
    On any failure: log AuditDeliveryFailure + send Cloudinary link as fallback text.
    """
    from apps.audit.gateway import AuditMediaGateway
    from apps.organizations.models import Organization

    try:
        audit = PropertyAudit.objects.get(pk=audit_id)
        org = Organization.objects.get(pk=org_id)
    except (PropertyAudit.DoesNotExist, Organization.DoesNotExist) as exc:
        logger.error("send_audit_via_whatsapp_task: object not found — %s", exc)
        return

    filename = f"PropertyAudit_{audit_id}.pdf"
    failure_type = 'wa_upload'

    try:
        pdf_bytes = _download_report(audit.cloudinary_url)

        media_id = AuditMediaGateway.register_with_meta(pdf_bytes, filename, org)

        failure_type = 'wa_send'
        AuditMediaGateway.dispatch_to_chat(
            phone, media_id, filename, org, caption='Your property audit report is ready.'
        )

        # Companion text with viral footer
        try:
            from apps.campaigns.viral_hooks import VirtualHookInjector
            from apps.notifications.delivery import enqueue_whatsapp_text
            companion = VirtualHookInjector.inject_footer('📊 Your full property audit is attached.', org)
            # T-1022b: outbox; a retried task does not repeat the companion text
            enqueue_whatsapp_text(phone, companion, key=f'audit_companion:{audit_id}',
                                  organization=org, skip_window_check=True)
        except Exception as _footer_exc:
            logger.warning('send_audit_via_whatsapp_task: footer send failed: %s', _footer_exc)

        audit.delivery_status = 'sent'
        audit.save(update_fields=['delivery_status'])

    except Exception as exc:
        logger.exception("send_audit_via_whatsapp_task failed audit_id=%s: %s", audit_id, exc)
        AuditDeliveryFailure.objects.create(
            audit=audit, failure_type=failure_type, error_detail=str(exc)
        )
        audit.delivery_status = 'failed'
        audit.save(update_fields=['delivery_status'])

        if audit.cloudinary_url:
            AuditMediaGateway.send_fallback_link(phone, audit.cloudinary_url, org)
