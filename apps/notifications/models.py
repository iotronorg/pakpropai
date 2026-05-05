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
    message    = models.TextField()
    status     = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    wa_message_id = models.CharField(max_length=100, blank=True)  # Meta's message ID for delivery tracking
    error      = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.channel} to {self.user.phone} — {self.status}"