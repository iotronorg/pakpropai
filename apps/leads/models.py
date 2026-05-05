import uuid
from django.db import models
from django.conf import settings


class Lead(models.Model):

    class Intent(models.TextChoices):
        BUY    = 'buy',    'Buying'
        SELL   = 'sell',   'Selling'
        RENT   = 'rent',   'Renting'
        INVEST = 'invest', 'Investing'
        LOAN   = 'loan',   'Loan Inquiry'
        TAX    = 'tax',    'Tax Advisory'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user           = models.ForeignKey(
                         settings.AUTH_USER_MODEL,
                         on_delete=models.CASCADE,
                         related_name='leads'
                     )
    intent         = models.CharField(max_length=20, choices=Intent.choices, null=True, blank=True)
    score          = models.SmallIntegerField(default=0)
    intent_signals = models.JSONField(default=dict, blank=True)
    city_interest  = models.CharField(max_length=100, blank=True)
    budget_min     = models.BigIntegerField(null=True, blank=True)
    budget_max     = models.BigIntegerField(null=True, blank=True)
    notes          = models.TextField(blank=True)
    last_scored_at = models.DateTimeField(auto_now=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'leads'
        ordering = ['-score', '-created_at']

    def __str__(self):
        return f"Lead: {self.user.phone} — score {self.score}"