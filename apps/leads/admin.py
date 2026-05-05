from django.contrib import admin
from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display  = ('user', 'intent', 'score', 'city_interest', 'last_scored_at')
    list_filter   = ('intent',)
    search_fields = ('user__phone', 'city_interest')
    readonly_fields = ('id', 'created_at', 'last_scored_at')