"""Listing moderation queue, user reports and appeals (T-2014, G2, ADR-040)."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ModerationItem(models.Model):
    class Reason(models.TextChoices):
        REVIEW   = 'review',   'Waiting for review'
        EDITED   = 'edited',   'Private listing edited'
        REPORTED = 'reported', 'Reported by users'
        APPEAL   = 'appeal',   'Appeal against a suspension'

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        APPROVED  = 'approved',  'Approved'
        REJECTED  = 'rejected',  'Returned to the lister'
        SUSPENDED = 'suspended', 'Taken down'
        DISMISSED = 'dismissed', 'No action'
        WITHDRAWN = 'withdrawn', 'Withdrawn by the lister'

    class ReasonCode(models.TextChoices):
        INCOMPLETE  = 'incomplete',  'Incomplete or unclear'
        MISLEADING  = 'misleading',  'Misleading or wrong facts'
        PRICE       = 'price',       'Unrealistic price'
        DUPLICATE   = 'duplicate',   'Duplicate listing'
        PROHIBITED  = 'prohibited',  'Prohibited content'
        FRAUD       = 'fraud',       'Suspected fraud'
        UNAVAILABLE = 'unavailable', 'No longer available'
        OTHER       = 'other',       'Other'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing    = models.ForeignKey('properties.Property', on_delete=models.CASCADE, related_name='moderation_items')
    reason     = models.CharField(max_length=10, choices=Reason.choices)
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    flags      = models.JSONField(default=list, blank=True)        # codes of the automated checks
    priority   = models.PositiveSmallIntegerField(default=0)       # higher first
    due_at     = models.DateTimeField()
    appeal_text = models.CharField(max_length=1000, blank=True)
    content_digest = models.CharField(max_length=64, blank=True)   # the text that was decided on
    decided_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    decided_at  = models.DateTimeField(null=True, blank=True)
    reason_code = models.CharField(max_length=12, choices=ReasonCode.choices, blank=True)
    note        = models.CharField(max_length=500, blank=True)     # shown to the lister
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moderation_items'
        ordering = ['-priority', 'due_at']
        indexes = [models.Index(fields=['status', '-priority', 'due_at'], name='moderation_queue_idx'),
                   models.Index(fields=['listing', '-decided_at'], name='moderation_listing_idx')]
        constraints = [models.UniqueConstraint(fields=['listing'], condition=Q(status='open'),
                                               name='moderation_one_open_item')]

    def __str__(self):
        return f'{self.reason} {self.listing_id} ({self.status})'


class UserReport(models.Model):
    class Category(models.TextChoices):
        FRAUD       = 'fraud',       'Scam or fraud'
        WRONG       = 'wrong',       'Wrong information'
        UNAVAILABLE = 'unavailable', 'No longer available'
        DUPLICATE   = 'duplicate',   'Duplicate'
        OFFENSIVE   = 'offensive',   'Offensive or discriminatory'
        OTHER       = 'other',       'Other'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='listing_reports')
    listing    = models.ForeignKey('properties.Property', on_delete=models.CASCADE, related_name='user_reports')
    item       = models.ForeignKey(ModerationItem, on_delete=models.CASCADE, related_name='reports')
    category   = models.CharField(max_length=12, choices=Category.choices)
    text       = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_reports'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['reporter', 'item'], name='one_report_per_item')]

    def __str__(self):
        return f'{self.category} {self.listing_id}'
