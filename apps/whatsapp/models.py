import uuid
from django.db import models
from django.conf import settings


class WhatsAppSession(models.Model):
    """
    Persistent record of WhatsApp conversations.
    Redis holds the live session; this table is the audit log.
    """
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone        = models.CharField(max_length=20, db_index=True)
    user         = models.ForeignKey(
                       settings.AUTH_USER_MODEL,
                       on_delete=models.SET_NULL,
                       null=True, blank=True,
                       related_name='wa_sessions'
                   )
    state        = models.CharField(max_length=50, default='IDLE')
    context      = models.JSONField(default=dict, blank=True)
    message_count = models.PositiveIntegerField(default=0)
    started_at   = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now=True)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='org_wa_sessions',
        db_index=True,
        help_text="Organization that owns this session's WhatsApp number",
    )

    class Meta:
        db_table = 'whatsapp_sessions'
        ordering = ['-last_message_at']

    def __str__(self):
        return f"WA Session: {self.phone} — {self.state}"


class WhatsAppMessage(models.Model):
    """Full message log for debugging and audit."""

    class Direction(models.TextChoices):
        INBOUND  = 'inbound',  'Inbound'
        OUTBOUND = 'outbound', 'Outbound'

    class MsgType(models.TextChoices):
        TEXT     = 'text',     'Text'
        AUDIO    = 'audio',    'Audio'
        IMAGE    = 'image',    'Image'
        DOCUMENT = 'document', 'Document'
        TEMPLATE = 'template', 'Template'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session       = models.ForeignKey(WhatsAppSession, on_delete=models.CASCADE, related_name='messages')
    wa_message_id = models.CharField(max_length=100, unique=True)  # Meta's message ID
    direction     = models.CharField(max_length=10, choices=Direction.choices)
    msg_type      = models.CharField(max_length=20, choices=MsgType.choices, default=MsgType.TEXT)
    body          = models.TextField(blank=True)
    # WhatsApp media object ID — populated for audio, image, and document messages.
    # Use this ID with WhatsAppClient.download_media() for deferred/lazy media processing.
    media_id      = models.CharField(max_length=100, blank=True, db_index=True)
    media_url     = models.URLField(blank=True)
    raw_payload   = models.JSONField(default=dict)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'whatsapp_messages'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.direction} {self.msg_type} — {self.session.phone}"


class OrgWhatsAppConfig(models.Model):
    _SECRET_KEYS = frozenset({'access_token', 'app_secret', 'verify_token'})

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='whatsapp_config',
    )
    phone_number_id   = models.CharField(max_length=50, blank=True, db_index=True,
                            help_text="Meta phone_number_id for this org's WA number")
    display_phone     = models.CharField(max_length=20, blank=True,
                            help_text="Human-readable E.164 phone number, e.g. +923001234567")
    access_token      = models.CharField(max_length=500, blank=True,
                            help_text="Meta System User token — never returned by API")
    app_id            = models.CharField(max_length=50, blank=True,
                            help_text="Meta App ID")
    app_secret        = models.CharField(max_length=200, blank=True,
                            help_text="Meta App Secret for HMAC-SHA256 webhook verification — never returned by API")
    verify_token      = models.CharField(max_length=200, blank=True,
                            help_text="Custom webhook verify token — never returned by API")
    is_active         = models.BooleanField(default=False,
                            help_text="Enable/disable this integration")
    webhook_verified_at = models.DateTimeField(null=True, blank=True,
                            help_text="Set when Meta successfully verifies the webhook")
    ai_enabled        = models.BooleanField(default=True,
                            help_text="Toggle AI automation for this org's WhatsApp")
    auto_reply_enabled = models.BooleanField(default=True,
                            help_text="Toggle auto-replies")
    otp_template_name = models.CharField(max_length=100, blank=True,
                            help_text="Per-org OTP template name (overrides platform default)")
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'org_whatsapp_configs'

    def __str__(self):
        return f"WA Config: {self.organization.name} ({self.display_phone or self.phone_number_id or 'unconfigured'})"
