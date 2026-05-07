from django.contrib import admin
from .models import PropertyAudit


@admin.register(PropertyAudit)
class PropertyAuditAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'city',
        'location',
        'property_type',
        'risk_score',
        'investment_grade',
        'liquidity_score',
        'created_at',
    )
    list_filter = ('investment_grade', 'city')
    search_fields = ('location', 'city', 'property_type', 'owner_name', 'phone')
    readonly_fields = ('created_at', 'audit_data')
    ordering = ('-created_at',)
