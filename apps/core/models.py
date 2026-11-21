import json
import uuid

from django.db import models

from apps.core.crypto import EncryptedTextField
from django.conf import settings


class AuditLog(models.Model):
    """Immutable record of every admin write action."""

    class Action(models.TextChoices):
        CREATE  = 'create',  'Create'
        UPDATE  = 'update',  'Update'
        DELETE  = 'delete',  'Delete'
        LOGIN   = 'login',   'Login'
        LOGOUT  = 'logout',  'Logout'
        APPROVE = 'approve', 'Approve'
        REJECT  = 'reject',  'Reject'
        BAN     = 'ban',     'Ban / Suspend'
        CONFIG  = 'config',  'Config Change'

    actor        = models.ForeignKey(
                       settings.AUTH_USER_MODEL,
                       on_delete=models.SET_NULL,
                       null=True, blank=True,
                       related_name='audit_logs',
                   )
    action       = models.CharField(max_length=20, choices=Action.choices)
    target_model = models.CharField(max_length=100, blank=True)
    target_id    = models.CharField(max_length=100, blank=True)
    detail       = models.TextField(blank=True, help_text='Human-readable summary')
    before       = models.JSONField(null=True, blank=True, help_text='State before change')
    after        = models.JSONField(null=True, blank=True, help_text='State after change')
    ip_address   = models.GenericIPAddressField(null=True, blank=True)
    user_agent   = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['actor', 'created_at']),
            models.Index(fields=['target_model', 'target_id']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        actor = self.actor.phone if self.actor else 'system'
        return f"[{self.action}] {self.target_model}:{self.target_id} by {actor}"

    @classmethod
    def log(cls, *, actor, action, target=None, detail='', before=None, after=None, request=None):
        """
        Convenience factory. Call from views or middleware:
            AuditLog.log(actor=request.user, action=AuditLog.Action.DELETE,
                         target=user_obj, detail='Admin deleted user')
        """
        target_model = target.__class__.__name__ if target else ''
        target_id    = str(target.pk) if target else ''
        ip   = cls._get_ip(request) if request else None
        ua   = request.headers.get('User-Agent', '') if request else ''

        return cls.objects.create(
            actor=actor,
            action=action,
            target_model=target_model,
            target_id=target_id,
            detail=detail,
            before=before,
            after=after,
            ip_address=ip,
            user_agent=ua,
        )

    @staticmethod
    def _get_ip(request):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class OutboxMessage(models.Model):
    """
    Transactional outbox for outbound side effects (ADR-014).

    A row is written in the same DB transaction as the business change that
    causes the side effect, then dispatched by ``apps.core.tasks`` (on commit,
    plus a periodic relay as a safety net). The payload is encrypted at rest
    because it can carry OTP codes, phone numbers and message text; the broker
    only ever sees the row id.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENDING = 'sending', 'Sending'
        SENT    = 'sent',    'Sent'
        FAILED  = 'failed',  'Failed (permanent or attempts exhausted)'
        EXPIRED = 'expired', 'Expired before delivery'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind            = models.CharField(max_length=50, help_text='Handler key, e.g. whatsapp.otp')
    idempotency_key = models.CharField(max_length=200, unique=True)
    organization_id = models.UUIDField(null=True, blank=True, db_index=True)
    payload         = EncryptedTextField(default='{}', help_text='JSON, encrypted at rest')
    status          = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts        = models.PositiveSmallIntegerField(default=0)
    max_attempts    = models.PositiveSmallIntegerField(default=5)
    available_at    = models.DateTimeField(help_text='Earliest time of the next attempt')
    locked_until    = models.DateTimeField(null=True, blank=True,
                                           help_text='Lease of the worker currently sending')
    expires_at      = models.DateTimeField(null=True, blank=True,
                                           help_text='Do not deliver after this time')
    last_error      = models.TextField(blank=True)
    provider_ref    = models.CharField(max_length=200, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    sent_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'outbox_messages'
        indexes  = [models.Index(fields=['status', 'available_at'], name='outbox_due_idx')]

    def __str__(self):
        return f'{self.kind} [{self.status}] {self.idempotency_key}'
