"""
Agent availability and viewing feedback (T-2060, ADR-037, FR-APT-001/006).
The booking itself is ``leads.Appointment``. Slots are computed, not stored.
"""
from django.db import models
from django.db.models import F, Q


class AvailabilityRule(models.Model):
    """Weekly hours, in the rule's own time zone."""
    agent        = models.ForeignKey('agents.Agent', on_delete=models.CASCADE, related_name='availability_rules')
    weekday      = models.PositiveSmallIntegerField(help_text='0 = Monday')
    start        = models.TimeField()
    end          = models.TimeField()
    timezone     = models.CharField(max_length=64, help_text='IANA, e.g. Asia/Karachi')
    slot_minutes = models.PositiveSmallIntegerField(default=60)

    class Meta:
        db_table = 'availability_rules'
        ordering = ['weekday', 'start']
        constraints = [models.CheckConstraint(condition=Q(end__gt=F('start')), name='availability_rule_end_after_start'),
                       models.CheckConstraint(condition=Q(weekday__lte=6), name='availability_rule_weekday')]

    def __str__(self):
        return f'{self.weekday} {self.start}-{self.end}'


class AvailabilityException(models.Model):
    """A date that differs from the weekly rules. No times = the whole day."""
    agent     = models.ForeignKey('agents.Agent', on_delete=models.CASCADE, related_name='availability_exceptions')
    date      = models.DateField()
    available = models.BooleanField(default=False)
    start     = models.TimeField(null=True, blank=True)
    end       = models.TimeField(null=True, blank=True)
    timezone  = models.CharField(max_length=64)

    class Meta:
        db_table = 'availability_exceptions'
        ordering = ['date', 'start']
        indexes = [models.Index(fields=['agent', 'date'], name='availability_exc_idx')]

    def __str__(self):
        return f'{self.date} {"open" if self.available else "closed"}'


class ViewingFeedback(models.Model):
    class Side(models.TextChoices):
        USER  = 'user',  'Visitor'
        AGENT = 'agent', 'Agent'

    appointment = models.ForeignKey('leads.Appointment', on_delete=models.CASCADE, related_name='feedback')
    side        = models.CharField(max_length=5, choices=Side.choices)
    author      = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, related_name='viewing_feedback')
    rating      = models.PositiveSmallIntegerField()
    interested  = models.BooleanField(null=True, blank=True)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'viewing_feedback'
        constraints = [models.UniqueConstraint(fields=['appointment', 'side'], name='viewing_feedback_once_per_side'),
                       models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name='viewing_feedback_rating')]

    def __str__(self):
        return f'{self.side} {self.rating}/5'
