"""
Market entity (T-1050a, ADR-019).

A market is a country the product operates in, with its own currency, area
units, language, time zone and formats. Rows are seeded from the code
registry (``apps/markets/registry.py``) by migration ``markets 0002``; market
behaviour that is code (calculators, parsers, document types, prompts) stays
in the ``apps/markets/<cc>/`` packs. Read through ``apps.markets.services``,
not directly, so the cache and the registry fallback apply.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


class Market(models.Model):

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active (launched)'
        BETA     = 'beta',     'Beta (configured, not launched)'
        INACTIVE = 'inactive', 'Inactive'

    code              = models.CharField(max_length=2, primary_key=True,
                            help_text='ISO 3166-1 alpha-2, upper-case, e.g. PK')
    name              = models.CharField(max_length=100)
    status            = models.CharField(max_length=10, choices=Status.choices, default=Status.BETA)
    default_currency  = models.CharField(max_length=3, help_text='ISO 4217')
    currencies        = models.JSONField(default=list, help_text='Accepted ISO 4217 codes')
    currency_symbol   = models.CharField(max_length=8, blank=True)
    default_area_unit = models.CharField(max_length=10, help_text='Property.AreaUnit value')
    area_units        = models.JSONField(default=list)
    default_language  = models.CharField(max_length=10, help_text='BCP-47')
    languages         = models.JSONField(default=list)
    timezone          = models.CharField(max_length=64, help_text='IANA, e.g. Asia/Karachi')
    price_format      = models.CharField(max_length=200, blank=True,
                            help_text='How locals express prices (LLM context)')
    tax_system        = models.CharField(max_length=300, blank=True)
    settings          = models.JSONField(default=dict, blank=True,
                            help_text='Market-specific flags and overrides')
    listing_schema    = models.JSONField(default=dict, db_default={}, blank=True,
                            help_text='Validates Property.attributes for listings in this market '
                                      '(format: apps/markets/listing_schema.py); {} = no rules')
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'markets'
        ordering = ['code']

    def clean(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        errors = {}
        if self.code != (self.code or '').upper() or len(self.code or '') != 2:
            errors['code'] = 'Two upper-case letters (ISO 3166-1 alpha-2).'
        if self.default_currency not in (self.currencies or []):
            errors['default_currency'] = 'Must be one of currencies.'
        if self.default_area_unit not in (self.area_units or []):
            errors['default_area_unit'] = 'Must be one of area_units.'
        if self.default_language not in (self.languages or []):
            errors['default_language'] = 'Must be one of languages.'
        try:
            ZoneInfo(self.timezone or '')
        except (ZoneInfoNotFoundError, ValueError):
            errors['timezone'] = 'Unknown IANA time zone.'
        from .listing_schema import schema_errors
        if (schema_problems := schema_errors(self.listing_schema)):
            errors['listing_schema'] = schema_problems
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.code} — {self.name} [{self.status}]'


class FxRate(models.Model):
    """Daily snapshot for approximate display conversion only; stored prices never change (T-2041)."""
    currency = models.CharField(max_length=3, validators=[RegexValidator(r'^[A-Z]{3}$', 'ISO 4217, upper-case.')])
    per_usd  = models.DecimalField(max_digits=20, decimal_places=8, validators=[MinValueValidator(Decimal('0.00000001'))],
                                   help_text='Units of this currency for 1 USD')
    as_of    = models.DateField()
    source   = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'fx_rates'
        ordering = ['-as_of', 'currency']
        constraints = [
            models.UniqueConstraint(fields=['currency', 'as_of'], name='fx_rate_once_a_day'),
            models.CheckConstraint(condition=models.Q(per_usd__gt=0), name='fx_rate_positive'),
        ]

    def __str__(self):
        return f'{self.currency} {self.per_usd} @ {self.as_of}'
