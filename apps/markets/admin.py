from django.contrib import admin

from .models import FxRate, Market


@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    """Edits are validated by Market.clean() and invalidate the market cache (ADR-019)."""
    list_display = ('code', 'name', 'status', 'default_currency', 'default_area_unit',
                    'default_language', 'timezone', 'updated_at')
    list_filter = ('status',)
    search_fields = ('code', 'name')
    readonly_fields = ('created_at', 'updated_at')

    def has_delete_permission(self, request, obj=None):
        # Listings and organisations reference markets (PROTECT); retire with status instead.
        return False


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'per_usd', 'as_of', 'source')
    list_filter = ('currency',)
    date_hierarchy = 'as_of'
