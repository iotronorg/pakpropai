from django.contrib import admin

from .models import Conversation


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Metadata only: message content is not shown in the admin."""
    list_display = ('id', 'channel', 'organization', 'market_code', 'language', 'is_open', 'last_message_at')
    list_filter = ('channel', 'market_code', 'is_open')
    list_select_related = ('organization',)
    fields = readonly_fields = ('id', 'channel', 'organization', 'market_code', 'language', 'is_open',
                                'created_at', 'last_message_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
