from django.contrib import admin
from .models import AuditBenchmark, PropertyAudit


@admin.register(AuditBenchmark)
class AuditBenchmarkAdmin(admin.ModelAdmin):
    list_display = (
        'city',
        'location_key',
        'ppm_min',
        'ppm_max',
        'yield_pct',
        'appr_pct',
        'liq_months',
        'approved',
        'is_active',
        'updated_at',
    )
    list_filter = ('city', 'is_active', 'approved')
    search_fields = ('city', 'location_key')
    list_editable = ('ppm_min', 'ppm_max', 'yield_pct', 'appr_pct', 'liq_months', 'approved', 'is_active')
    readonly_fields = ('updated_at', 'updated_by')
    ordering = ('city', 'location_key')
    fieldsets = (
        ('Location', {'fields': ('city', 'location_key', 'approved', 'is_active')}),
        ('Price Benchmarks (PKR / Marla)', {'fields': ('ppm_min', 'ppm_max')}),
        ('Market Metrics', {'fields': ('yield_pct', 'appr_pct', 'liq_months')}),
        ('Audit Trail', {'fields': ('updated_at', 'updated_by'), 'classes': ('collapse',)}),
    )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


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
