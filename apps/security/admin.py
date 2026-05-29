from django.contrib import admin
from apps.security.models import ApiSecurityEvent


@admin.register(ApiSecurityEvent)
class ApiSecurityEventAdmin(admin.ModelAdmin):
    list_display    = ('created_at', 'event_type', 'severity', 'ip_address',
                       'organization_id', 'endpoint', 'http_method')
    list_filter     = ('event_type', 'severity')
    search_fields   = ('ip_address', 'user_id', 'organization_id', 'threat_detail')
    readonly_fields = [f.name for f in ApiSecurityEvent._meta.get_fields()
                       if hasattr(f, 'name')]
    ordering        = ('-created_at',)

    def has_add_permission(self, request):              return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
