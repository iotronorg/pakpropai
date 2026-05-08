from django.contrib import admin
from django.utils.html import format_html
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display   = ('user', 'amount_fmt', 'purpose', 'gateway', 'status_badge', 'reference', 'deal_link', 'created_at')
    list_filter    = ('status', 'purpose', 'gateway')
    search_fields  = ('user__phone', 'reference', 'checkout_token')
    readonly_fields = ('id', 'checkout_token', 'checkout_url', 'webhook_payload', 'created_at', 'updated_at')
    ordering       = ('-created_at',)

    def amount_fmt(self, obj):
        return f"PKR {obj.amount_pkr:,}"
    amount_fmt.short_description = 'Amount'

    def status_badge(self, obj):
        colors = {'pending': '#f59e0b', 'completed': '#10b981', 'failed': '#ef4444', 'refunded': '#6366f1'}
        color  = colors.get(obj.status, '#000')
        return format_html('<span style="color:{};font-weight:bold">{}</span>', color, obj.get_status_display())
    status_badge.short_description = 'Status'

    def deal_link(self, obj):
        if obj.escrow_deal_id:
            return format_html(
                '<a href="/admin/escrow/escrowdeal/{}/change/">{}</a>',
                obj.escrow_deal_id, str(obj.escrow_deal_id)[:8].upper()
            )
        return '—'
    deal_link.short_description = 'Deal'