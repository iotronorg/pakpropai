from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):

    list_display = (
        'name', 'agent_type_badge', 'phone', 'company_name',
        'cities_str', 'specializations_str',
        'verified_badge', 'featured_badge', 'is_active',
        'rating', 'total_leads', 'closed_deals',
    )
    list_filter  = ('agent_type', 'is_verified', 'is_active', 'is_featured', 'primary_city')
    search_fields = ('name', 'phone', 'email', 'company_name', 'cnic_number', 'license_number')
    readonly_fields = (
        'joined_at', 'updated_at', 'total_leads', 'total_listings',
        'closed_deals', 'last_active_at',
    )
    list_editable = ('is_active',)
    ordering      = ('-is_featured', '-is_verified', '-rating', 'name')

    fieldsets = (
        ('Identity', {
            'fields': (
                'name', 'agent_type', 'profile_photo',
                'phone', 'whatsapp_number', 'email', 'cnic_number',
            ),
        }),
        ('Professional Details', {
            'fields': (
                'company_name', 'designation', 'license_number',
                'years_experience', 'languages', 'bio',
            ),
        }),
        ('Specializations & Coverage', {
            'fields': (
                'specializations', 'primary_city', 'cities', 'areas',
            ),
            'description': (
                'specializations: list of keys — residential_buy, residential_rent, '
                'commercial, plots, new_projects, luxury, industrial<br>'
                'cities: e.g. ["Lahore", "Islamabad"]<br>'
                'areas: e.g. ["DHA Phase 5 Lahore", "F-7 Islamabad"]'
            ),
        }),
        ('Business Information', {
            'classes': ('collapse',),
            'fields': (
                'registration_number', 'ntn_number',
                'website', 'office_address',
                'instagram_handle', 'facebook_page',
            ),
        }),
        ('Status & Verification', {
            'fields': (
                'is_verified', 'is_active', 'is_featured',
                'verified_at', 'verified_by',
                'internal_notes',
            ),
        }),
        ('Performance Metrics', {
            'classes': ('collapse',),
            'fields': (
                'total_leads', 'total_listings', 'closed_deals',
                'rating', 'last_active_at',
            ),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('joined_at', 'updated_at'),
        }),
    )

    # ── Custom list display helpers ────────────────────────────────────────────

    @admin.display(description='Type')
    def agent_type_badge(self, obj):
        colours = {
            'individual': '#1B4F72',
            'developer':  '#27AE60',
            'agency':     '#8E44AD',
        }
        colour = colours.get(obj.agent_type, '#666')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px">{}</span>',
            colour, obj.get_agent_type_display()
        )

    @admin.display(description='Verified', boolean=False)
    def verified_badge(self, obj):
        if obj.is_verified:
            return format_html('<span style="color:#27AE60;font-weight:bold">✓ Verified</span>')
        return format_html('<span style="color:#E74C3C">✗ Unverified</span>')

    @admin.display(description='Featured', boolean=False)
    def featured_badge(self, obj):
        if obj.is_featured:
            return format_html('<span style="color:#F39C12;font-weight:bold">★ Featured</span>')
        return '—'

    # ── Admin actions ──────────────────────────────────────────────────────────

    actions = ['mark_verified', 'mark_unverified', 'mark_featured', 'mark_unfeatured', 'deactivate']

    @admin.action(description='Mark selected agents as Verified')
    def mark_verified(self, request, queryset):
        queryset.update(is_verified=True, verified_at=timezone.now(), verified_by=request.user)
        self.message_user(request, f"{queryset.count()} agent(s) verified.")

    @admin.action(description='Remove verification from selected agents')
    def mark_unverified(self, request, queryset):
        queryset.update(is_verified=False, verified_at=None, verified_by=None)
        self.message_user(request, f"{queryset.count()} agent(s) unverified.")

    @admin.action(description='Mark as Featured')
    def mark_featured(self, request, queryset):
        queryset.update(is_featured=True)
        self.message_user(request, f"{queryset.count()} agent(s) marked as featured.")

    @admin.action(description='Remove Featured status')
    def mark_unfeatured(self, request, queryset):
        queryset.update(is_featured=False)
        self.message_user(request, f"{queryset.count()} agent(s) unfeatured.")

    @admin.action(description='Deactivate selected agents')
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f"{queryset.count()} agent(s) deactivated.")
