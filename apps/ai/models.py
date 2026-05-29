import uuid
from django.db import models
from django.conf import settings


class TokenUsageRecord(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org        = models.ForeignKey(
                     'organizations.Organization',
                     on_delete=models.CASCADE,
                     related_name='token_usage_records',
                 )
    tokens_in  = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    model      = models.CharField(max_length=80, default='gemini-1.5-flash')
    intent     = models.CharField(max_length=60, blank=True, null=True)
    cache_hit  = models.BooleanField(default=False)
    timestamp  = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'ai_token_usage_records'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['org', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.org_id} — {self.model} in={self.tokens_in} out={self.tokens_out} cache={self.cache_hit}"


class AIInteraction(models.Model):
    """
    Logs every Gemini call — for cost tracking, debugging, and cache analysis.
    """
    class InteractionType(models.TextChoices):
        INTENT_CLASSIFY = 'intent_classify', 'Intent Classification'
        PROPERTY_SCORE  = 'property_score',  'Property Scoring'
        OCR             = 'ocr',             'Document OCR'
        TAX_ADVISORY    = 'tax_advisory',     'Tax Advisory'
        LOAN_CHECK      = 'loan_check',       'Loan Check'
        FRAUD_CHECK     = 'fraud_check',      'Fraud Check'
        VOICE_TRANSCRIBE = 'voice_transcribe', 'Voice Transcription'
        REPORT_GEN      = 'report_gen',       'Report Generation'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user             = models.ForeignKey(
                           settings.AUTH_USER_MODEL,
                           on_delete=models.SET_NULL,
                           null=True, blank=True,
                           related_name='ai_interactions'
                       )
    interaction_type = models.CharField(max_length=30, choices=InteractionType.choices)
    prompt_tokens    = models.PositiveIntegerField(default=0)
    response_tokens  = models.PositiveIntegerField(default=0)
    model_used       = models.CharField(max_length=50, default='gemini-1.5-flash')
    was_cached       = models.BooleanField(default=False)
    response_ms      = models.PositiveIntegerField(default=0)   # latency
    input_data       = models.JSONField(default=dict, blank=True)
    output_data      = models.JSONField(default=dict, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_interactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.interaction_type} — {self.model_used} ({self.prompt_tokens}+{self.response_tokens} tokens)"