from django.contrib import admin
from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display  = ['name', 'organization', 'status', 'audience_filter',
                     'recipient_count', 'sent_count', 'failed_count', 'scheduled_at', 'created_at']
    list_filter   = ['status', 'audience_filter']
    search_fields = ['name', 'organization__name']
    readonly_fields = ['id', 'recipient_count', 'sent_count', 'failed_count', 'sent_at',
                       'created_at', 'updated_at']
    raw_id_fields = ['organization', 'created_by']
