from django.contrib import admin
from .models import EscrowDeal


@admin.register(EscrowDeal)
class EscrowDealAdmin(admin.ModelAdmin):
    list_display  = ('property', 'buyer', 'seller', 'token_amount', 'status', 'lock_expires_at', 'created_at')
    list_filter   = ('status',)
    search_fields = ('property__title', 'buyer__phone', 'seller__phone')
    readonly_fields = ('id', 'created_at', 'updated_at')