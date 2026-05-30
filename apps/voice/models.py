import uuid
from django.db import models
from django.conf import settings


class VoiceCallSession(models.Model):

    class Direction(models.TextChoices):
        INBOUND  = 'inbound',  'Inbound'
        OUTBOUND = 'outbound', 'Outbound'

    class Status(models.TextChoices):
        RINGING      = 'ringing',      'Ringing'
        AI_HANDLING  = 'ai_handling',  'AI Handling'
        AGENT_JOINED = 'agent_joined', 'Agent Joined'
        COMPLETED    = 'completed',    'Completed'
        FAILED       = 'failed',       'Failed'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='voice_calls',
    )
    lead = models.ForeignKey(
        'leads.Lead', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='voice_calls',
    )
    call_sid    = models.CharField(max_length=64, unique=True, db_index=True)
    from_phone  = models.CharField(max_length=20)
    to_phone    = models.CharField(max_length=20)
    direction   = models.CharField(max_length=10, choices=Direction.choices)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.RINGING, db_index=True)
    barge_in_at    = models.DateTimeField(null=True, blank=True)
    barge_in_agent = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='barge_in_calls',
    )
    transcript           = models.TextField(blank=True)
    ai_context_snapshot  = models.JSONField(default=dict, blank=True)
    recording_url        = models.URLField(blank=True)
    started_at           = models.DateTimeField(null=True, blank=True)
    ended_at             = models.DateTimeField(null=True, blank=True)
    duration_seconds     = models.IntegerField(null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'voice_call_sessions'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['from_phone']),
        ]

    def __str__(self):
        return f"Call {self.call_sid} [{self.status}] {self.from_phone}→{self.to_phone}"


class OrgVoiceConfig(models.Model):
    _SENSITIVE = frozenset({'auth_token'})

    organization  = models.OneToOneField(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='voice_config',
    )
    account_sid   = models.CharField(max_length=100, blank=True)
    auth_token    = models.CharField(max_length=100, blank=True)
    phone_number  = models.CharField(max_length=20, blank=True,
                        help_text='E.164 Twilio phone number, e.g. +15551234567')
    is_active     = models.BooleanField(default=False)
    record_calls  = models.BooleanField(default=False)
    ai_voice_name = models.CharField(max_length=50, default='Polly.Joanna',
                        help_text='Twilio TTS voice, e.g. Polly.Joanna, Polly.Amy')
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'org_voice_configs'

    def __str__(self):
        return f"VoiceConfig({self.organization.name})"
