import uuid
from django.db import models
from django.conf import settings


class Notification(models.Model):

    class Channel(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        SMS      = 'sms',      'SMS'
        EMAIL    = 'email',    'Email'

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        SENT      = 'sent',      'Sent'
        FAILED    = 'failed',    'Failed'
        DELIVERED = 'delivered', 'Delivered'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.CASCADE,
                     related_name='notifications'
                 )
    channel    = models.CharField(max_length=20, choices=Channel.choices, default=Channel.WHATSAPP)
    title      = models.CharField(max_length=200, blank=True)
    message    = models.TextField()
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_read    = models.BooleanField(default=False, db_index=True)
    wa_message_id = models.CharField(max_length=100, blank=True)
    error      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.channel} to {self.user.phone} — {self.status}"


class UserNotificationPreference(models.Model):
    """Per-user opt-in/opt-out for notification channels and event types."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )

    # Channel opt-outs
    whatsapp_enabled = models.BooleanField(default=True)
    sms_enabled      = models.BooleanField(default=True)
    email_enabled    = models.BooleanField(default=True)

    # Event type opt-outs
    lead_updates          = models.BooleanField(default=True, help_text='Lead status and assignment changes')
    appointment_reminders = models.BooleanField(default=True, help_text='Upcoming visit reminders')
    deal_updates          = models.BooleanField(default=True, help_text='Deal lock and escrow notifications')
    report_ready          = models.BooleanField(default=True, help_text='Report generation completed')
    marketing             = models.BooleanField(default=False, help_text='Promotional and marketing messages')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_notification_preferences'

    def __str__(self):
        return f"NotificationPrefs for {self.user.phone}"