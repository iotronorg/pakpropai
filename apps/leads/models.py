import uuid
from django.db import models
from django.conf import settings


class Lead(models.Model):

    class Source(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        REFERRAL = 'referral', 'Referral'
        WEB      = 'web',      'Web Portal'
        MANUAL   = 'manual',   'Manual Entry'

    class Intent(models.TextChoices):
        BUY    = 'buy',    'Buying'
        SELL   = 'sell',   'Selling'
        RENT   = 'rent',   'Renting'
        INVEST = 'invest', 'Investing'
        LOAN   = 'loan',   'Loan Inquiry'
        TAX    = 'tax',    'Tax Advisory'

    class Status(models.TextChoices):
        NEW       = 'new',       'New'
        WARM      = 'warm',      'Warm'
        QUALIFIED = 'qualified', 'Qualified'
        COLD      = 'cold',      'Cold'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.ForeignKey(
                         settings.AUTH_USER_MODEL,
                         on_delete=models.CASCADE,
                         related_name='leads'
                     )
    assigned_agent = models.ForeignKey(
                         'agents.Agent',
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='assigned_leads',
                         help_text='Agent responsible for following up this lead'
                     )
    intent         = models.CharField(max_length=20, choices=Intent.choices, null=True, blank=True)
    score          = models.SmallIntegerField(default=0)
    intent_signals = models.JSONField(default=dict, blank=True)
    city_interest  = models.CharField(max_length=100, blank=True)
    budget_min     = models.BigIntegerField(null=True, blank=True)
    budget_max     = models.BigIntegerField(null=True, blank=True)
    source              = models.CharField(max_length=20, choices=Source.choices, default=Source.WHATSAPP)
    status              = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    notes               = models.TextField(blank=True)
    last_contacted_at   = models.DateTimeField(null=True, blank=True)
    last_scored_at      = models.DateTimeField(auto_now=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'leads'
        ordering = ['-score', '-created_at']

    def __str__(self):
        return f"Lead: {self.user.phone} — score {self.score}"


class LeadActivity(models.Model):
    """Chronological timeline of all events on a lead."""

    class ActionType(models.TextChoices):
        CREATED    = 'created',    'Lead Created'
        ASSIGNED   = 'assigned',   'Agent Assigned'
        STATUS     = 'status',     'Status Changed'
        NOTE       = 'note',       'Note Added'
        CONTACTED  = 'contacted',  'Client Contacted'
        SCORED     = 'scored',     'Score Updated'
        DEAL_LOCK  = 'deal_lock',  'Deal Lock Initiated'

    lead       = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='activities')
    actor      = models.ForeignKey(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.SET_NULL,
                     null=True, blank=True,
                     related_name='lead_activities',
                 )
    action     = models.CharField(max_length=20, choices=ActionType.choices)
    notes      = models.TextField(blank=True)
    meta       = models.JSONField(default=dict, blank=True, help_text='e.g. {"old_status": "new", "new_status": "warm"}')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_activities'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action} on Lead {self.lead_id}"


class Appointment(models.Model):
    """Property visit or meeting scheduled between a lead and an agent."""

    class Status(models.TextChoices):
        SCHEDULED  = 'scheduled',  'Scheduled'
        CONFIRMED  = 'confirmed',  'Confirmed'
        COMPLETED  = 'completed',  'Completed'
        CANCELLED  = 'cancelled',  'Cancelled'
        NO_SHOW    = 'no_show',    'No Show'
        RESCHEDULED = 'rescheduled', 'Rescheduled'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead         = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='appointments')
    property     = models.ForeignKey(
                       'properties.Property',
                       on_delete=models.SET_NULL,
                       null=True, blank=True,
                       related_name='appointments',
                   )
    agent        = models.ForeignKey(
                       'agents.Agent',
                       on_delete=models.SET_NULL,
                       null=True, blank=True,
                       related_name='appointments',
                   )
    scheduled_at      = models.DateTimeField()
    duration_minutes  = models.PositiveSmallIntegerField(default=60)
    status            = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    notes             = models.TextField(blank=True)
    reminder_sent_at  = models.DateTimeField(null=True, blank=True)
    created_by        = models.ForeignKey(
                            settings.AUTH_USER_MODEL,
                            on_delete=models.SET_NULL,
                            null=True, blank=True,
                            related_name='created_appointments',
                        )
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'appointments'
        ordering = ['scheduled_at']
        indexes  = [
            models.Index(fields=['scheduled_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Appointment [{self.status}] — {self.lead} @ {self.scheduled_at:%Y-%m-%d %H:%M}"


class ConversationMessage(models.Model):
    """CRM message log — one record per message exchanged with a lead."""

    class Direction(models.TextChoices):
        INBOUND  = 'inbound',  'Inbound (from client)'
        OUTBOUND = 'outbound', 'Outbound (to client)'

    class Channel(models.TextChoices):
        WHATSAPP  = 'whatsapp',  'WhatsApp'
        DASHBOARD = 'dashboard', 'Dashboard Note'

    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead      = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=10, choices=Direction.choices)
    channel   = models.CharField(max_length=20, choices=Channel.choices,
                                 default=Channel.WHATSAPP)
    body      = models.TextField()
    sender    = models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=models.SET_NULL,
                    null=True, blank=True,
                    related_name='sent_crm_messages',
                    help_text='Set for outbound dashboard messages; null for inbound/AI replies'
                )
    wa_message_id = models.CharField(max_length=200, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'conversation_messages'
        ordering = ['created_at']

    def __str__(self):
        return f"{self.direction} [{self.channel}] — lead {self.lead_id}"