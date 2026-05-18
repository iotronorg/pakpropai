from django.db import models


class AuditBenchmark(models.Model):
    """
    Admin-configurable market benchmarks used by AuditEngine to score properties.
    Global-first: country + city + location_key uniquely identify each row.
    size_unit and currency are stored per-row so the engine can serve multiple markets.
    """

    class SizeUnit(models.TextChoices):
        MARLA = 'marla', 'Marla'
        KANAL = 'kanal', 'Kanal'
        SQFT  = 'sqft',  'Square Feet'
        SQM   = 'sqm',   'Square Metre'
        ACRE  = 'acre',  'Acre'

    country      = models.CharField(
                       max_length=2, default='PK',
                       help_text='ISO 3166-1 alpha-2 country code, e.g. PK, AE, UK',
                   )
    city         = models.CharField(max_length=100, help_text='e.g. lahore, islamabad, dubai, default')
    location_key = models.CharField(max_length=100, help_text='e.g. dha, bahria, gulberg, default')

    # ── Unit-agnostic price benchmarks ────────────────────────────────────────
    price_per_unit_min = models.BigIntegerField(
                             help_text='Min price per size_unit in the local currency',
                         )
    price_per_unit_max = models.BigIntegerField(
                             help_text='Max price per size_unit in the local currency',
                         )
    size_unit    = models.CharField(
                       max_length=10,
                       choices=SizeUnit.choices,
                       default=SizeUnit.MARLA,
                       help_text='Unit that price_per_unit_min/max is denominated in',
                   )
    currency     = models.CharField(
                       max_length=3, default='PKR',
                       help_text='ISO 4217 currency code for all price fields in this row',
                   )

    yield_pct    = models.FloatField(help_text='Expected rental yield %')
    appr_pct     = models.FloatField(help_text='Expected annual appreciation %')
    liq_months   = models.IntegerField(help_text='Average months to sell')
    approved     = models.BooleanField(
                       null=True, blank=True,
                       help_text='Is the area authority-approved? Leave blank if unknown.',
                   )
    is_active    = models.BooleanField(default=True)
    updated_at   = models.DateTimeField(auto_now=True)
    updated_by   = models.ForeignKey(
                       'users.User', null=True, blank=True, on_delete=models.SET_NULL,
                       related_name='benchmark_updates',
                   )

    class Meta:
        unique_together = ('country', 'city', 'location_key')
        ordering = ['country', 'city', 'location_key']
        indexes  = [
            models.Index(fields=['country', 'city']),
        ]
        verbose_name = 'Audit Benchmark'
        verbose_name_plural = 'Audit Benchmarks'

    def __str__(self):
        return f"[{self.country}] {self.city} / {self.location_key} ({self.currency}/{self.size_unit})"

    def to_dict(self) -> dict:
        return {
            # 'ppm' key preserved for backward compatibility with AuditEngine
            'ppm':            (self.price_per_unit_min, self.price_per_unit_max),
            'price_per_unit': (self.price_per_unit_min, self.price_per_unit_max),
            'size_unit':      self.size_unit,
            'currency':       self.currency,
            'yield_pct':      self.yield_pct,
            'appr_pct':       self.appr_pct,
            'liq_months':     self.liq_months,
            'approved':       self.approved,
        }


class PropertyAudit(models.Model):

    class InvestmentGrade(models.TextChoices):
        A = 'A', 'Excellent'
        B = 'B', 'Good'
        C = 'C', 'Fair'
        D = 'D', 'Poor'

    user = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audits',
    )
    phone         = models.CharField(max_length=20, blank=True)
    city          = models.CharField(max_length=100)
    location      = models.CharField(max_length=200)
    property_type = models.CharField(max_length=100)

    # ── Size — db_column preserves column name; Python attr is market-neutral ──
    area_size     = models.DecimalField(
                        max_digits=8, decimal_places=2, null=True, blank=True,
                        help_text='Numeric area in the unit specified by area_unit',
                    )
    area_unit     = models.CharField(
                        max_length=10,
                        choices=[
                            ('marla', 'Marla'), ('kanal', 'Kanal'), ('sqft', 'Sq Ft'),
                            ('sqm', 'Sq Metre'), ('acre', 'Acre'),
                        ],
                        default='marla',
                    )

    # ── Estimated value — currency stored alongside ────────────────────────────
    estimated_value = models.BigIntegerField(
                          help_text='Estimated property value in the currency specified by currency',
                      )
    currency        = models.CharField(
                          max_length=3, default='PKR',
                          help_text='ISO 4217 currency code for estimated_value',
                      )

    owner_name  = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)

    # Scores
    risk_score = models.IntegerField(default=5, help_text='1–10; 1 = safest, 10 = highest risk')
    investment_grade = models.CharField(
        max_length=1,
        choices=InvestmentGrade.choices,
        default=InvestmentGrade.C,
    )
    liquidity_score = models.IntegerField(default=5, help_text='1–10; 10 = most liquid')

    # Full structured report
    audit_data = models.JSONField(default=dict)

    # Generated PDF
    pdf_file = models.FileField(upload_to='audits/', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"{self.property_type} — {self.location}, {self.city} "
            f"({self.get_investment_grade_display()})"
        )
