from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display  = ('user', 'channel', 'status', 'created_at', 'sent_at')
    list_filter   = ('channel', 'status')
    search_fields = ('user__phone', 'message')
    readonly_fields = ('id', 'created_at', 'sent_at')