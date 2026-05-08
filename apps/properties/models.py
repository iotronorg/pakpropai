import uuid
from django.db import models
from django.conf import settings


class Property(models.Model):

    class PropertyType(models.TextChoices):
        RESIDENTIAL = 'residential', 'Residential'
        COMMERCIAL  = 'commercial',  'Commercial'
        PLOT        = 'plot',        'Plot'
        INDUSTRIAL  = 'industrial',  'Industrial'

    class LegalStatus(models.TextChoices):
        UNVERIFIED = 'unverified', 'Unverified'
        VERIFIED   = 'verified',   'Verified'
        DISPUTED   = 'disputed',   'Disputed'
        PENDING    = 'pending',    'Pending Verification'

    class RiskLevel(models.TextChoices):
        LOW    = 'low',    'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH   = 'high',   'High'

    class FurnishedStatus(models.TextChoices):
        FURNISHED       = 'furnished',       'Furnished'
        UNFURNISHED     = 'unfurnished',     'Unfurnished'
        SEMI_FURNISHED  = 'semi_furnished',  'Semi-Furnished'

    class ConstructionStatus(models.TextChoices):
        BUILDER_NEW      = 'builder',        'Builder / New'
        READY            = 'ready',          'Ready'
        UNDER_CONSTRUCTION = 'under_construction', 'Under Construction'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner         = models.ForeignKey(
                        settings.AUTH_USER_MODEL,
                        on_delete=models.SET_NULL,
                        null=True, blank=True,
                        related_name='properties'
                    )
    title         = models.CharField(max_length=500)
    description   = models.TextField(blank=True)
    city          = models.CharField(max_length=100)
    location      = models.CharField(max_length=300)
    area_marla    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_pkr     = models.BigIntegerField(null=True, blank=True)
    property_type        = models.CharField(max_length=30, choices=PropertyType.choices, default=PropertyType.RESIDENTIAL)
    furnished_status     = models.CharField(max_length=20, choices=FurnishedStatus.choices, null=True, blank=True)
    construction_status  = models.CharField(max_length=25, choices=ConstructionStatus.choices, null=True, blank=True)
    legal_status         = models.CharField(max_length=30, choices=LegalStatus.choices, default=LegalStatus.UNVERIFIED)
    assigned_agent = models.ForeignKey(
                         'agents.Agent',
                         on_delete=models.SET_NULL,
                         null=True, blank=True,
                         related_name='assigned_properties',
                         help_text='Agent responsible for selling this property'
                     )
    ai_score      = models.SmallIntegerField(null=True, blank=True)
    risk_level    = models.CharField(max_length=20, choices=RiskLevel.choices, null=True, blank=True)
    raw_docs      = models.JSONField(default=dict, blank=True)   # Cloudflare R2 keys
    ai_analysis   = models.JSONField(default=dict, blank=True)   # Gemini response cache
    is_active     = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'properties'
        ordering  = ['-created_at']
        indexes   = [
            models.Index(fields=['city']),
            models.Index(fields=['ai_score']),
            models.Index(fields=['legal_status']),
            models.Index(fields=['owner']),
        ]

    def __str__(self):
        return f"{self.title} — {self.city}"