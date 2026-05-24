"""
AuditMediaGateway — tenant-isolated Cloudinary storage, Meta media upload,
WhatsApp document dispatch, and Cloudinary-link fallback.
"""
import io
import logging
import uuid

logger = logging.getLogger(__name__)


class AuditMediaGateway:

    @classmethod
    def upload_to_cloudinary(cls, pdf_bytes: bytes, audit_id: int, org_id: str) -> str:
        """
        Upload PDF to Cloudinary under an unguessable tenant-keyed path.
        Path: audits/{org_id}/{uuid4}/{audit_id}.pdf
        Returns the secure CDN URL.
        """
        import cloudinary.uploader
        folder = f"audits/{org_id}/{uuid.uuid4().hex}"
        result = cloudinary.uploader.upload(
            io.BytesIO(pdf_bytes),
            folder=folder,
            public_id=f"audit_{audit_id}",
            resource_type='raw',
            overwrite=False,
        )
        return result['secure_url']

    @classmethod
    def register_with_meta(cls, pdf_bytes: bytes, filename: str, org) -> str:
        """
        Upload PDF binary to the WhatsApp Cloud API media endpoint.
        Returns the document_id (media_id) assigned by Meta.
        Raises requests.HTTPError on non-2xx.
        """
        from apps.whatsapp.client import get_wa_client
        client = get_wa_client(org)
        return client.upload_media(pdf_bytes, filename=filename)

    @classmethod
    def dispatch_to_chat(
        cls, phone: str, media_id: str, filename: str, org, caption: str = ''
    ) -> dict:
        """
        Send the uploaded document to the user's WhatsApp chat.
        Returns the Meta API response dict.
        Raises ValueError (outside 24h window) or requests.HTTPError on failure.
        """
        from apps.whatsapp.client import get_wa_client
        client = get_wa_client(org)
        return client.send_document(phone, media_id, filename=filename, caption=caption)

    @classmethod
    def send_fallback_link(cls, phone: str, cloudinary_url: str, org) -> None:
        """
        Send the Cloudinary download URL as a free-form text message.
        Logs (but does not re-raise) if the text send also fails.
        """
        from apps.whatsapp.client import get_wa_client
        client = get_wa_client(org)
        body = (
            "Your property audit report is ready.\n\n"
            f"Download here: {cloudinary_url}\n\n"
            "_Link is valid for 30 days._"
        )
        try:
            client.send_text(phone, body, skip_window_check=True)
        except Exception as exc:
            logger.error("Fallback link send failed to %s: %s", phone, exc)
