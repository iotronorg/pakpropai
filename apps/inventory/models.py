import uuid

from django.conf import settings
from django.db import models


class ExternalPlatformConnection(models.Model):

    class Platform(models.TextChoices):
        ZAMEEN         = 'zameen',         'Zameen'
        PROPERTYFINDER = 'propertyfinder', 'PropertyFinder'
        BAYUT          = 'bayut',          'Bayut'
        RIGHTMOVE      = 'rightmove',      'Rightmove'
        ZILLOW         = 'zillow',         'Zillow'
        CUSTOM         = 'custom',         'Custom'

    class SyncDirection(models.TextChoices):
        INBOUND       = 'inbound',       'Inbound Only'
        OUTBOUND      = 'outbound',      'Outbound Only'
        BIDIRECTIONAL = 'bidirectional', 'Bidirectional'

    class ConflictResolution(models.TextChoices):
        INTERNAL_WINS = 'internal_wins', 'Internal Wins'
        EXTERNAL_WINS = 'external_wins', 'External Wins'
        MANUAL        = 'manual',        'Manual Review'

    class SyncStatus(models.TextChoices):
        IDLE    = 'idle',    'Idle'
        SYNCING = 'syncing', 'Syncing'
        ERROR   = 'error',   'Error'
        PAUSED  = 'paused',  'Paused'

    id                  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org                 = models.ForeignKey(
                              'organizations.Organization',
                              on_delete=models.CASCADE,
                              related_name='platform_connections',
                          )
    platform            = models.CharField(max_length=20, choices=Platform.choices)
    api_key             = models.CharField(max_length=500, blank=True)
    api_secret          = models.CharField(max_length=500, blank=True)
    base_url            = models.URLField(blank=True, help_text='Required for custom platform')
    is_active           = models.BooleanField(default=True)
    sync_direction      = models.CharField(
                              max_length=15,
                              choices=SyncDirection.choices,
                              default=SyncDirection.INBOUND,
                          )
    conflict_resolution = models.CharField(
                              max_length=15,
                              choices=ConflictResolution.choices,
                              default=ConflictResolution.INTERNAL_WINS,
                          )
    last_synced_at      = models.DateTimeField(null=True, blank=True)
    sync_status         = models.CharField(
                              max_length=10,
                              choices=SyncStatus.choices,
                              default=SyncStatus.IDLE,
                              db_index=True,
                          )
    error_detail        = models.TextField(blank=True)
    field_mappings      = models.JSONField(default=dict, blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'inventory_platform_connections'
        ordering = ['platform', 'created_at']

    def __str__(self):
        return f"{self.platform} — {self.org.name}"


class SyncConflictAlert(models.Model):

    class Resolution(models.TextChoices):
        PENDING       = 'pending',       'Pending'
        INTERNAL_WINS = 'internal_wins', 'Internal Wins'
        EXTERNAL_WINS = 'external_wins', 'External Wins'
        MANUAL        = 'manual',        'Manual'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org            = models.ForeignKey(
                         'organizations.Organization',
                         on_delete=models.CASCADE,
                         related_name='sync_conflict_alerts',
                     )
    connection     = models.ForeignKey(
                         ExternalPlatformConnection,
                         on_delete=models.CASCADE,
                         related_name='conflict_alerts',
                     )
    property       = models.ForeignKey(
                         'properties.Property',
                         on_delete=models.CASCADE,
                         related_name='sync_conflicts',
                     )
    external_delta = models.JSONField(default=dict)
    internal_state = models.JSONField(default=dict)
    resolution     = models.CharField(
                         max_length=15,
                         choices=Resolution.choices,
                         default=Resolution.PENDING,
                         db_index=True,
                     )
    created_at     = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at    = models.DateTimeField(null=True, blank=True)
    resolved_by    = models.ForeignKey(
                         settings.AUTH_USER_MODEL,
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='resolved_sync_conflicts',
                     )

    class Meta:
        db_table = 'inventory_sync_conflict_alerts'
        ordering = ['-created_at']

    def __str__(self):
        return f"SyncConflict [{self.resolution}] — {self.property}"


class WebhookDeliveryRecord(models.Model):

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        DELIVERED = 'delivered', 'Delivered'
        FAILED    = 'failed',    'Failed'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org           = models.ForeignKey(
                        'organizations.Organization',
                        on_delete=models.CASCADE,
                        related_name='webhook_deliveries',
                    )
    connection    = models.ForeignKey(
                        ExternalPlatformConnection,
                        on_delete=models.CASCADE,
                        related_name='webhook_records',
                    )
    event_type    = models.CharField(max_length=50, blank=True)
    payload       = models.JSONField(default=dict)
    status        = models.CharField(
                        max_length=10,
                        choices=Status.choices,
                        default=Status.PENDING,
                        db_index=True,
                    )
    attempt_count = models.PositiveIntegerField(default=0)
    delivered_at  = models.DateTimeField(null=True, blank=True)
    error_detail  = models.CharField(max_length=500, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'inventory_webhook_delivery_records'
        ordering = ['-created_at']

    def __str__(self):
        return f"WebhookDelivery [{self.status}] — {self.connection}"
