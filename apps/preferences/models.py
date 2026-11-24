"""What a person keeps: favourites, saved searches, and a consented preference profile (T-2040, ADR-038)."""
import uuid

from django.conf import settings
from django.db import models


class Favourite(models.Model):
    class Kind(models.TextChoices):
        SAVED     = 'saved',     'Saved'
        DISMISSED = 'dismissed', 'Not interested'

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='favourites')
    listing    = models.ForeignKey('properties.Property', on_delete=models.CASCADE, related_name='favourited_by')
    kind       = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SAVED)
    note       = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'favourites'
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['user', 'kind', '-updated_at'], name='favourite_user_idx')]
        constraints = [models.UniqueConstraint(fields=['user', 'listing'], name='favourite_once')]

    def __str__(self):
        return f'{self.kind} {self.listing_id}'


class SavedSearch(models.Model):
    class Frequency(models.TextChoices):
        INSTANT = 'instant', 'As it happens (at most hourly)'
        DAILY   = 'daily',   'Daily'
        WEEKLY  = 'weekly',  'Weekly'

    class Channel(models.TextChoices):
        IN_APP   = 'in_app',   'In app'
        EMAIL    = 'email',    'Email'
        WHATSAPP = 'whatsapp', 'WhatsApp'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_searches')
    name       = models.CharField(max_length=100)
    criteria   = models.JSONField()                       # output of criteria.prepare
    frequency  = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.DAILY)
    channel    = models.CharField(max_length=10, choices=Channel.choices, default=Channel.IN_APP)
    is_active  = models.BooleanField(default=True)
    paused_reason = models.CharField(max_length=20, blank=True, default='')     # user | unsubscribed | criteria
    consented_at  = models.DateTimeField()
    consent_via   = models.CharField(max_length=20)
    since      = models.DateTimeField()                   # only what became available after this is new
    next_run_at   = models.DateTimeField(db_index=True)
    last_alert_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'saved_searches'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['is_active', 'next_run_at'], name='saved_search_due_idx')]

    def __str__(self):
        return f'saved search {self.id}'


class AlertDelivery(models.Model):
    """A listing is announced once per saved search."""
    saved_search = models.ForeignKey(SavedSearch, on_delete=models.CASCADE, related_name='deliveries')
    listing      = models.ForeignKey('properties.Property', on_delete=models.CASCADE, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'alert_deliveries'
        constraints = [models.UniqueConstraint(fields=['saved_search', 'listing'], name='alert_once')]

    def __str__(self):
        return f'{self.saved_search_id} {self.listing_id}'


class PreferenceItem(models.Model):
    """Kept only while the person's ``ai_memory`` consent is active."""
    class Source(models.TextChoices):
        STATED   = 'stated',   'Stated'
        INFERRED = 'inferred', 'Inferred from saved listings'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preference_items')
    market     = models.ForeignKey('markets.Market', on_delete=models.CASCADE, related_name='+')
    key        = models.CharField(max_length=30)          # a SearchCriteria field
    value      = models.JSONField()
    source     = models.CharField(max_length=10, choices=Source.choices)
    confidence = models.FloatField(default=1.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'preference_items'
        ordering = ['market_id', 'key']
        constraints = [models.UniqueConstraint(fields=['user', 'market', 'key', 'source'], name='preference_item_once')]

    def __str__(self):
        return f'{self.key} ({self.source})'
