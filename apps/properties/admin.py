# Register your models here.
from django.contrib import admin
from .models import Property


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display  = ('title', 'city', 'property_type', 'price_pkr', 'ai_score', 'legal_status', 'owner', 'created_at')
    list_filter   = ('city', 'property_type', 'legal_status', 'risk_level')
    search_fields = ('title', 'city', 'location', 'owner__phone')
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering      = ('-created_at',)