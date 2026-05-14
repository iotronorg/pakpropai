from rest_framework import serializers
from .models import Notification, UserNotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Notification
        fields = ('id', 'channel', 'title', 'message', 'status', 'is_read', 'created_at', 'sent_at')
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model  = UserNotificationPreference
        fields = (
            'whatsapp_enabled', 'sms_enabled', 'email_enabled',
            'lead_updates', 'appointment_reminders', 'deal_updates',
            'report_ready', 'marketing', 'updated_at',
        )
        read_only_fields = ('updated_at',)
