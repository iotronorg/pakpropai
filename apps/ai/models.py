import uuid
from django.db import models
from django.conf import settings

from apps.core.crypto import EncryptedTextField


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
        CHAT            = 'chat',             'Conversation turn'
        LEAD_SUMMARY    = 'lead_summary',     'Lead summary'
        REPLY_SUGGEST   = 'reply_suggest',    'Reply suggestions'
        RE_ENGAGEMENT   = 're_engagement',    'Re-engagement message'

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
    # T-1120: which provider answered and which registered prompt produced the input.
    provider         = models.CharField(max_length=20, blank=True, default='', db_default='')
    prompt_name      = models.CharField(max_length=80, blank=True, default='', db_default='')
    prompt_version   = models.PositiveSmallIntegerField(null=True, blank=True)
    prompt_digest    = models.CharField(max_length=12, blank=True, default='', db_default='')
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

class PendingAction(models.Model):
    """
    A side-effect tool call waiting for (or resolved by) the user's explicit
    confirmation (T-2012, ADR-030, MVP acceptance 8).

    Written by ``apps.ai.confirmation`` only. The stored arguments are what
    runs: the model cannot change them after the user has seen ``prompt``.
    """
    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        EXECUTING  = 'executing',  'Executing'
        EXECUTED   = 'executed',   'Executed'
        FAILED     = 'failed',     'Failed'
        DECLINED   = 'declined',   'Declined'
        SUPERSEDED = 'superseded', 'Superseded'
        EXPIRED    = 'expired',    'Expired'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # "<org id or 'global'>:<phone>", the same scope as the conversation history.
    conversation     = models.ForeignKey('conversations.Conversation', on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='pending_actions')
    conversation_key = models.CharField(max_length=80, db_index=True)
    organization     = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='ai_pending_actions')
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='ai_pending_actions')
    turn_id          = models.CharField(max_length=32, blank=True, default='')
    tool             = models.CharField(max_length=60)
    side_effect      = models.CharField(max_length=20)
    args             = EncryptedTextField()             # JSON
    args_hash        = models.CharField(max_length=64)
    prompt           = EncryptedTextField()             # exactly what the user was shown
    status           = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    # True only until the next message: a later bare "yes" answers something else.
    awaiting_reply   = models.BooleanField(default=True)
    confirmed_via    = models.CharField(max_length=10, blank=True, default='')   # message | api
    result           = models.JSONField(default=dict, blank=True)                # non-personal outcome only
    created_at       = models.DateTimeField(auto_now_add=True)
    expires_at       = models.DateTimeField()
    confirmed_at     = models.DateTimeField(null=True, blank=True)
    resolved_at      = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ai_pending_actions'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'status'], name='ai_pending_user_status'),
                   models.Index(fields=['created_at'], name='ai_pending_created')]
        constraints = [models.UniqueConstraint(fields=['conversation_key'],
                                               condition=models.Q(status='pending'),
                                               name='ai_pending_one_per_conversation')]

    def __str__(self):
        return f'{self.tool} [{self.status}]'

    @property
    def phone(self) -> str:
        contact = self.conversation_key.split(':', 1)[1]
        if contact.startswith('web-'):           # web chats have no phone in the key
            return (self.user.phone or '').lstrip('+') if self.user else ''
        return contact


class ModelPrice(models.Model):
    """
    What a provider charges for a model (T-2011), entered by operators from the
    provider's price list. Nothing is seeded: an unpriced call has no cost
    rather than a guessed one. A price change is a new row.
    """
    model           = models.CharField(max_length=80)
    effective_from  = models.DateField()
    currency        = models.CharField(max_length=3, default='USD')
    input_per_mtok  = models.DecimalField(max_digits=12, decimal_places=6)
    cached_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True,
                                          help_text='Cached input; empty = same as input')
    output_per_mtok = models.DecimalField(max_digits=12, decimal_places=6)

    class Meta:
        db_table = 'ai_model_prices'
        ordering = ['model', '-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['model', 'effective_from'], name='ai_model_price_unique'),
            models.CheckConstraint(condition=models.Q(input_per_mtok__gte=0, output_per_mtok__gte=0)
                                   & (models.Q(cached_per_mtok__isnull=True) | models.Q(cached_per_mtok__gte=0)),
                                   name='ai_model_price_non_negative'),
        ]

    def __str__(self):
        return f'{self.model} from {self.effective_from}'


class AITrace(models.Model):
    """One assistant turn: route, prompt, usage, cost, checks (AI spec §10). No message text."""
    class Route(models.TextChoices):
        LLM           = 'llm',           'Model'
        DIRECT        = 'direct',        'Deterministic'
        CONFIRMATION  = 'confirmation',  'Confirmation reply'
        CACHE         = 'cache',         'Semantic cache'
        GUARD_BLOCKED = 'guard_blocked', 'Blocked input'
        THROTTLED     = 'throttled',     'Budget throttle'

    class Pricing(models.TextChoices):
        NO_CALLS = 'no_calls', 'No model calls'
        PRICED   = 'priced',   'Priced'
        UNPRICED = 'unpriced', 'Price or usage missing'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization     = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE,
                                         null=True, blank=True, related_name='ai_traces')
    user             = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='ai_traces')
    conversation_ref = models.CharField(max_length=64, blank=True, default='')   # keyed hash, not the phone
    interactive      = models.BooleanField(default=True)
    route            = models.CharField(max_length=15, choices=Route.choices, default=Route.LLM)
    intent           = models.CharField(max_length=60, blank=True, default='')
    prompt_name      = models.CharField(max_length=80, blank=True, default='')
    prompt_version   = models.PositiveSmallIntegerField(null=True, blank=True)
    prompt_digest    = models.CharField(max_length=12, blank=True, default='')
    provider         = models.CharField(max_length=20, blank=True, default='')
    model            = models.CharField(max_length=80, blank=True, default='')
    llm_calls        = models.PositiveSmallIntegerField(default=0)
    tokens_in        = models.PositiveIntegerField(default=0)
    tokens_out       = models.PositiveIntegerField(default=0)
    cached_tokens    = models.PositiveIntegerField(default=0)
    pricing          = models.CharField(max_length=10, choices=Pricing.choices, default=Pricing.NO_CALLS)
    cost             = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True)
    currency         = models.CharField(max_length=3, blank=True, default='')
    latency_ms       = models.PositiveIntegerField(default=0)
    tool_calls       = models.JSONField(default=list, blank=True)     # name/status/ms only, no arguments
    guard_results    = models.JSONField(default=list, blank=True)
    # passed | corrected | fallback | error (runtime, T-2013); skipped (proposal); blocked (legacy check)
    grounding_result = models.CharField(max_length=10, blank=True, default='')
    grounding_codes  = models.JSONField(default=list, blank=True)     # violation codes of the first draft
    citations        = models.JSONField(default=list, blank=True)     # listing ids the reply used (FR-AI-007)
    fallback_used    = models.BooleanField(default=False)
    error            = models.CharField(max_length=80, blank=True, default='')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_traces'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'created_at'], name='ai_trace_org_created'),
                   models.Index(fields=['created_at'], name='ai_trace_created')]

    def __str__(self):
        return f'{self.route} {self.model} {self.created_at:%Y-%m-%d %H:%M}'
