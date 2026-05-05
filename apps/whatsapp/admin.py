from django.contrib import admin
from .models import WhatsAppSession, WhatsAppMessage


@admin.register(WhatsAppSession)
class WhatsAppSessionAdmin(admin.ModelAdmin):
    list_display  = ('phone', 'user', 'state', 'message_count', 'last_message_at')
    list_filter   = ('state',)
    search_fields = ('phone', 'user__phone')
    readonly_fields = ('id', 'started_at', 'last_message_at')


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display  = ('session', 'direction', 'msg_type', 'body', 'created_at')
    list_filter   = ('direction', 'msg_type')
    search_fields = ('session__phone', 'body')
    readonly_fields = ('id', 'created_at')