from django.db import models


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
