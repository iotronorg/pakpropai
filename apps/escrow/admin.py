from django.contrib import admin
from django.utils.html import format_html
from .models import EscrowDeal


@admin.register(EscrowDeal)
class EscrowDealAdmin(admin.ModelAdmin):
    list_display  = (
        'property', 'buyer', 'token_amount', 'status_badge',
        'payment_gateway', 'payment_ref', 'lock_expires_at', 'hours_left', 'created_at',
    )
    list_filter   = ('status', 'payment_gateway', 'initiated_via')
    search_fields = ('property__title', 'buyer__phone', 'payment_ref')
    readonly_fields = ('id', 'lock_started_at', 'lock_expires_at', 'created_at', 'updated_at')
    ordering = ('-created_at',)

    def status_badge(self, obj):
        colors = {
            'initiated': '#f59e0b', 'locked': '#10b981', 'released': '#6366f1',
            'cancelled': '#6b7280', 'disputed': '#ef4444', 'expired': '#9ca3af',
        }
        color = colors.get(obj.status, '#000')
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>', color, obj.get_status_display()
        )
    status_badge.short_description = 'Status'

    def hours_left(self, obj):
        h = obj.hours_remaining()
        if h is None:
            return '—'
        return f"{h:.1f}h"
    hours_left.short_description = 'Hours Left'