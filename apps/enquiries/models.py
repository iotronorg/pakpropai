"""
Consented enquiries (T-2050, ADR-036, FR-COM-001). An enquiry is also its thread:
the messages are ``leads.ConversationMessage`` rows, so the CRM sees the same log.
Written through ``apps.enquiries.services`` only.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.crypto import EncryptedTextField


class Enquiry(models.Model):
    class Kind(models.TextChoices):            # who answers
        ORGANIZATION = 'organization', 'Organization'
        AGENT        = 'agent',        'Freelance agent'
        OWNER        = 'owner',        'Private owner'
        PLATFORM     = 'platform',     'Platform team'

    class Status(models.TextChoices):
        OPEN   = 'open',   'Open'
        CLOSED = 'closed', 'Closed'

    class Channel(models.TextChoices):
        IN_APP   = 'in_app',   'In app'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        EMAIL    = 'email',    'Email'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='enquiries')
    kind         = models.CharField(max_length=15, choices=Kind.choices)
    organization = models.ForeignKey('organizations.Organization', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='enquiries')
    responder    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='enquiries_to_answer')      # agent and owner kinds
    listing      = models.ForeignKey('properties.Property', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='enquiries')
    subject      = models.CharField(max_length=200)        # kept when the listing is removed
    requirement  = models.JSONField(default=dict, blank=True)
    lead         = models.ForeignKey('leads.Lead', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='enquiries')
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    preferred_channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.IN_APP)

    # Contact details are shown only while both sides share them.
    consent_version     = models.CharField(max_length=20)
    consent_via         = models.CharField(max_length=20)                     # api | assistant
    consented_at        = models.DateTimeField()
    consent_revoked_at  = models.DateTimeField(null=True, blank=True)
    responder_shared_at = models.DateTimeField(null=True, blank=True)

    user_last_at      = models.DateTimeField(null=True, blank=True)
    user_read_at      = models.DateTimeField(null=True, blank=True)
    responder_last_at = models.DateTimeField(null=True, blank=True)
    responder_read_at = models.DateTimeField(null=True, blank=True)
    last_message_at   = models.DateTimeField()
    created_at        = models.DateTimeField(auto_now_add=True)
    closed_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'enquiries'
        ordering = ['-last_message_at']
        indexes = [models.Index(fields=['user', '-last_message_at'], name='enquiry_user_idx'),
                   models.Index(fields=['organization', '-last_message_at'], name='enquiry_org_idx'),
                   models.Index(fields=['responder', '-last_message_at'], name='enquiry_responder_idx')]
        constraints = [models.UniqueConstraint(fields=['user', 'listing'], condition=Q(status='open'),
                                               name='enquiry_one_open_per_listing')]

    def __str__(self):
        return f'{self.kind} enquiry {self.id}'

    @property
    def contacts_shared(self) -> bool:
        return self.consent_revoked_at is None and self.responder_shared_at is not None


class MessageAttachment(models.Model):
    """A sanitised photo, encrypted in our database: never at a public media URL (T-2051)."""
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message      = models.ForeignKey('leads.ConversationMessage', on_delete=models.CASCADE, related_name='attachments')
    sender       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    content_type = models.CharField(max_length=20)
    size         = models.PositiveIntegerField()
    width        = models.PositiveIntegerField()
    height       = models.PositiveIntegerField()
    payload      = EncryptedTextField(help_text='Base64')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'message_attachments'
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['sender', '-created_at'], name='attachment_sender_idx')]

    def __str__(self):
        return f'{self.content_type} {self.id}'


class EnquiryBlock(models.Model):
    """One side of a consumer/lister pair stopped the contact. The lister is an organization or a person."""
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, null=True, blank=True,
                                     related_name='+')
    responder    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                     related_name='+')
    side         = models.CharField(max_length=10, choices=[('user', 'Consumer'), ('responder', 'Lister')])   # blocker
    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enquiry_blocks'
        constraints = [
            models.UniqueConstraint(fields=['user', 'organization', 'side'], condition=Q(organization__isnull=False),
                                    name='enquiry_block_org'),
            models.UniqueConstraint(fields=['user', 'responder', 'side'], condition=Q(responder__isnull=False),
                                    name='enquiry_block_responder')]

    def __str__(self):
        return f'Block by {self.side} {self.id}'


class ThreadReport(models.Model):
    class Category(models.TextChoices):
        FRAUD    = 'fraud',    'Scam or fraud'
        SPAM     = 'spam',     'Spam'
        ABUSE    = 'abuse',    'Abuse or harassment'
        DISCRIMINATION = 'discrimination', 'Discrimination'
        OTHER    = 'other',    'Other'

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        CLOSED    = 'closed',    'Thread closed'
        DISMISSED = 'dismissed', 'No action'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enquiry    = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name='reports')
    reporter   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='thread_reports')
    side       = models.CharField(max_length=10, choices=[('user', 'Consumer'), ('responder', 'Lister')])
    category   = models.CharField(max_length=15, choices=Category.choices)
    text       = models.CharField(max_length=1000, blank=True)
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    priority   = models.PositiveSmallIntegerField(default=0)
    due_at     = models.DateTimeField()
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    note       = models.CharField(max_length=500, blank=True)      # internal
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'thread_reports'
        ordering = ['-priority', 'due_at', 'id']
        indexes = [models.Index(fields=['status', '-priority', 'due_at'], name='thread_report_queue_idx')]
        constraints = [models.UniqueConstraint(fields=['enquiry', 'reporter'], condition=Q(status='open'),
                                               name='thread_report_one_open')]

    def __str__(self):
        return f'{self.category} {self.enquiry_id} ({self.status})'
