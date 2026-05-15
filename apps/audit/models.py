from django.db import models


class AuditBenchmark(models.Model):
    """
    Admin-configurable benchmarks used by AuditEngine to score properties.
    Pre-populated with Pakistani real-estate defaults; admin can update any row.
    city + location_key uniquely identify each benchmark (use 'default' for fallbacks).
    """
    city         = models.CharField(max_length=100, help_text="e.g. lahore, islamabad, karachi, default")
    location_key = models.CharField(max_length=100, help_text="e.g. dha, bahria, gulberg, default")
    ppm_min      = models.BigIntegerField(help_text="Min price per marla (PKR)")
    ppm_max      = models.BigIntegerField(help_text="Max price per marla (PKR)")
    yield_pct    = models.FloatField(help_text="Expected rental yield %")
    appr_pct     = models.FloatField(help_text="Expected annual appreciation %")
    liq_months   = models.IntegerField(help_text="Avg months to sell")
    approved     = models.BooleanField(
        null=True, blank=True,
        help_text="Is the area LDA/CDA/RDA approved? Leave blank if unknown.",
    )
    is_active    = models.BooleanField(default=True)
    updated_at   = models.DateTimeField(auto_now=True)
    updated_by   = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='benchmark_updates',
    )

    class Meta:
        unique_together = ('city', 'location_key')
        ordering = ['city', 'location_key']
        verbose_name = 'Audit Benchmark'
        verbose_name_plural = 'Audit Benchmarks'

    def __str__(self):
        return f"{self.city} / {self.location_key}"

    def to_dict(self):
        return {
            'ppm': (self.ppm_min, self.ppm_max),
            'yield_pct': self.yield_pct,
            'appr_pct': self.appr_pct,
            'liq_months': self.liq_months,
            'approved': self.approved,
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
    phone = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100)
    location = models.CharField(max_length=200)
    property_type = models.CharField(max_length=100)
    area_marla = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_value_pkr = models.BigIntegerField()
    owner_name = models.CharField(max_length=200, blank=True)
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
