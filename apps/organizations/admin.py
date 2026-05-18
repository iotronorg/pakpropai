from django.contrib import admin
from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display  = ('name', 'org_type', 'plan', 'country', 'city', 'is_active', 'is_verified', 'created_at')
    list_filter   = ('org_type', 'plan', 'country', 'is_active', 'is_verified')
    search_fields = ('name', 'slug', 'email', 'phone', 'city')
    readonly_fields = ('id', 'slug', 'created_at', 'updated_at')
    raw_id_fields   = ('admin_user',)
    fieldsets = (
        (None, {
            'fields': ('id', 'name', 'slug', 'org_type', 'admin_user', 'plan'),
        }),
        ('Contact', {
            'fields': ('phone', 'email', 'website', 'logo'),
        }),
        ('Location', {
            'fields': ('country', 'city', 'address'),
        }),
        ('Status', {
            'fields': ('is_active', 'is_verified'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
