"""
Consented enquiries (T-2050, ADR-036, FR-COM-001). An enquiry is also its thread:
the messages are ``leads.ConversationMessage`` rows, so the CRM sees the same log.
Written through ``apps.enquiries.services`` only.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


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
