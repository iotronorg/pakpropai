from django.contrib import admin
from .models import Verification


@admin.register(Verification)
class VerificationAdmin(admin.ModelAdmin):
    list_display  = ('property', 'requested_by', 'status', 'verified_at', 'created_at')
    list_filter   = ('status',)
    search_fields = ('property__title', 'requested_by__phone')
    readonly_fields = ('id', 'created_at', 'verified_at')