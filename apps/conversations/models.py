"""
Durable assistant conversations (T-2020, ADR-033, FR-AI-002/004).

Written through ``apps.conversations.store`` only. ``key`` is the scope the AI
layer uses everywhere (history, confirmations, grounding, traces):
``<org id|global>:<phone>`` on WhatsApp, ``<org id|global>:web-<uuid>`` on web.
"""
import uuid

from django.conf import settings
from django.db import models

from apps.core.crypto import EncryptedTextField


class Conversation(models.Model):
    class Channel(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        WEB      = 'web',      'Web / app'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key             = models.CharField(max_length=80, db_index=True)
    channel         = models.CharField(max_length=10, choices=Channel.choices)
    user            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                        null=True, blank=True, related_name='conversations')
    organization    = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE,
                                        null=True, blank=True, related_name='conversations')
    market_code     = models.CharField(max_length=2, blank=True, default='')
    language        = models.CharField(max_length=10, blank=True, default='')
    is_open         = models.BooleanField(default=True)       # a reset closes it; the next message opens a new one
    created_at      = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'ai_conversations'
        ordering = ['-last_message_at']
        indexes = [models.Index(fields=['user', 'channel', '-last_message_at'], name='ai_conv_user_channel')]
        constraints = [models.UniqueConstraint(fields=['key'], condition=models.Q(is_open=True),
                                               name='ai_conv_one_open_per_key')]

    def __str__(self):
        return f'{self.channel} {self.id}'


class Message(models.Model):
    class Role(models.TextChoices):
        USER      = 'user',      'User'
        ASSISTANT = 'assistant', 'Assistant'

    class Status(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        DONE       = 'done',       'Done'
        FAILED     = 'failed',     'Failed'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    seq          = models.PositiveIntegerField()                   # order within the conversation
    role         = models.CharField(max_length=10, choices=Role.choices)
    content      = EncryptedTextField(blank=True)
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.DONE)
    in_window    = models.BooleanField(default=True)               # shown to the model on later turns
    reply_to     = models.OneToOneField('self', on_delete=models.CASCADE, null=True, blank=True,
                                        related_name='reply')
    client_id    = models.UUIDField(null=True, blank=True)         # client_message_id (retry safety)
    citations    = models.JSONField(default=list, blank=True)      # listing ids
    # prompt version, model, tokens, latency and tool calls (PRD C.1 acceptance 3)
    trace        = models.ForeignKey('ai.AITrace', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='messages')
    pending_action = models.ForeignKey('ai.PendingAction', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='messages')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_messages'
        ordering = ['conversation', 'seq']
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'seq'], name='ai_msg_seq_unique'),
            models.UniqueConstraint(fields=['conversation', 'client_id'], name='ai_msg_client_id_unique',
                                    condition=models.Q(client_id__isnull=False)),
        ]

    def __str__(self):
        return f'{self.role} #{self.seq}'


class ConversationState(models.Model):
    """Session state, separate from the chat history (FR-AI-004)."""
    conversation    = models.OneToOneField(Conversation, on_delete=models.CASCADE, primary_key=True,
                                           related_name='state')
    active_criteria = models.JSONField(default=dict, blank=True)   # last search; SearchCriteria in T-2030
    last_results    = models.JSONField(default=list, blank=True)   # listing ids
    shortlist       = models.JSONField(default=list, blank=True)   # listing ids chosen by the user
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_conversation_states'
