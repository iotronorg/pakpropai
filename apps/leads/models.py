import uuid
from django.core.exceptions import ValidationError
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
        NEW               = 'new',               'New'
        WARM              = 'warm',              'Warm'
        QUALIFIED         = 'qualified',         'Qualified'
        COLD              = 'cold',              'Cold'
        BLOCKED_MALICIOUS = 'blocked_malicious', 'Blocked — Malicious'

    class RoutingState(models.TextChoices):
        AI_QUEUE       = 'ai_queue',       'AI Routing Queue (unscoped)'
        ORG_QUEUE      = 'org_queue',      'Organization Queue (org-scoped, unassigned)'
        AGENT_ASSIGNED = 'agent_assigned', 'Agent Assigned'
        CLOSED         = 'closed',         'Closed / Converted'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.ForeignKey(
                         settings.AUTH_USER_MODEL,
                         on_delete=models.CASCADE,
                         related_name='leads'
                     )
    organization   = models.ForeignKey(
                         'organizations.Organization',
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='leads',
                         help_text='Organization this lead is scoped to (null = platform-wide / AI queue)',
                     )
    assigned_agent = models.ForeignKey(
                         'agents.Agent',
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='assigned_leads',
                         help_text='Agent responsible for following up this lead',
                     )

    # ── AI routing state machine ────────────────────────────────────────────────
    routing_state  = models.CharField(
                         max_length=20,
                         choices=RoutingState.choices,
                         default=RoutingState.AI_QUEUE,
                         db_index=True,
                         help_text=(
                             'AI_QUEUE → unscoped; ORG_QUEUE → org-scoped but no agent yet; '
                             'AGENT_ASSIGNED → assigned; CLOSED → converted/disqualified'
                         ),
                     )
    priority       = models.SmallIntegerField(
                         default=0,
                         help_text='Higher = more urgent. AI sets this; agents/admins can override.',
                     )

    intent         = models.CharField(max_length=20, choices=Intent.choices, null=True, blank=True)
    score          = models.SmallIntegerField(default=0)
    intent_signals = models.JSONField(default=dict, blank=True)
    score_factors  = models.JSONField(default=dict, blank=True)
    city_interest  = models.CharField(max_length=100, blank=True)

    # ── Budget — stored with ISO 4217 currency code ─────────────────────────────
    budget_min      = models.BigIntegerField(null=True, blank=True)
    budget_max      = models.BigIntegerField(null=True, blank=True)
    budget_currency = models.CharField(
                          max_length=3, default='PKR',
                          help_text='ISO 4217 currency code for budget_min/max, e.g. PKR, AED, USD',
                      )

    source              = models.CharField(max_length=20, choices=Source.choices, default=Source.WHATSAPP)
    status              = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    notes               = models.TextField(blank=True)
    last_contacted_at   = models.DateTimeField(null=True, blank=True)
    follow_up_sent_at   = models.DateTimeField(null=True, blank=True)
    last_scored_at      = models.DateTimeField(auto_now=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'leads'
        ordering = ['-score', '-created_at']
        indexes  = [
            models.Index(fields=['routing_state']),
            models.Index(fields=['organization', 'routing_state']),
            models.Index(fields=['priority']),
            models.Index(fields=['user']),
            models.Index(fields=['user', '-created_at']),
        ]

    def clean(self):
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_min > self.budget_max:
                raise ValidationError({'budget_min': 'Minimum budget cannot exceed maximum budget.'})

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
        HANDOVER   = 'handover',   'Handover'

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


class LeadScoreHistory(models.Model):
    """Immutable log of every intent-score change on a lead."""

    lead       = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='score_history')
    old_score  = models.SmallIntegerField()
    new_score  = models.SmallIntegerField()
    changed_by = models.ForeignKey(
                     settings.AUTH_USER_MODEL,
                     on_delete=models.SET_NULL,
                     null=True, blank=True,
                     related_name='lead_score_changes',
                 )
    reason     = models.CharField(max_length=200, blank=True, help_text='e.g. "AI rescored", "manual override"')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_score_history'
        ordering = ['-created_at']

    def __str__(self):
        return f"Lead {self.lead_id}: {self.old_score} → {self.new_score}"


class CRMContact(models.Model):

    class Source(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        MANUAL   = 'manual',   'Manual Entry'
        IMPORT   = 'import',   'Bulk Import'

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='crm_contacts',
    )
    phone      = models.CharField(max_length=20, help_text='E.164 format')
    name       = models.CharField(max_length=100, blank=True)
    source     = models.CharField(max_length=20, choices=Source.choices, default=Source.WHATSAPP)
    tags       = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'crm_contacts'
        unique_together = [('organization', 'phone')]
        indexes         = [models.Index(fields=['organization', 'phone'])]

    def __str__(self):
        return f"{self.name or self.phone} ({self.organization.name})"


class ClientProfile(models.Model):
    contact            = models.OneToOneField(CRMContact, on_delete=models.CASCADE,
                             related_name='client_profile')
    wa_phone_number    = models.CharField(max_length=20)
    last_active_at     = models.DateTimeField(null=True, blank=True)
    preferred_lang     = models.CharField(max_length=10, default='en')
    conversation_count = models.IntegerField(default=0)

    class Meta:
        db_table = 'client_profiles'

    def __str__(self):
        return f"ClientProfile({self.wa_phone_number})"