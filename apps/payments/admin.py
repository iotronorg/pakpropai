from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display  = ('user', 'amount_pkr', 'purpose', 'status', 'reference', 'created_at')
    list_filter   = ('status', 'purpose')
    search_fields = ('user__phone', 'reference')
    readonly_fields = ('id', 'created_at', 'updated_at')