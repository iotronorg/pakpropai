from django.contrib import admin
from django.utils.html import format_html
from .models import Organization


def _make_plan_action(plan_value, plan_label):
    def action(modeladmin, request, queryset):
        updated = queryset.update(plan=plan_value)
        modeladmin.message_user(request, f"{updated} organization(s) upgraded to {plan_label}.")
    action.__name__ = f"set_plan_{plan_value}"
    action.short_description = f"Set plan → {plan_label}"
    return action


_upgrade_basic        = _make_plan_action(Organization.Plan.BASIC,        "Basic")
_upgrade_professional = _make_plan_action(Organization.Plan.PROFESSIONAL, "Professional")
_upgrade_enterprise   = _make_plan_action(Organization.Plan.ENTERPRISE,   "Enterprise")
_downgrade_trial      = _make_plan_action(Organization.Plan.TRIAL,        "Trial")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display    = ('name', 'org_type', 'plan_badge', 'country', 'city', 'is_active', 'is_verified', 'created_at')
    list_filter     = ('org_type', 'plan', 'country', 'is_active', 'is_verified')
    search_fields   = ('name', 'slug', 'email', 'phone', 'city')
    readonly_fields = ('id', 'slug', 'created_at', 'updated_at')
    raw_id_fields   = ('admin_user',)
    actions         = [_upgrade_basic, _upgrade_professional, _upgrade_enterprise, _downgrade_trial]
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

    @admin.display(description='Plan')
    def plan_badge(self, obj):
        colors = {
            'trial':        ('#6b7280', '#f3f4f6'),
            'basic':        ('#1d4ed8', '#dbeafe'),
            'professional': ('#7c3aed', '#ede9fe'),
            'enterprise':   ('#065f46', '#d1fae5'),
        }
        fg, bg = colors.get(obj.plan, ('#374151', '#f9fafb'))
        return format_html(
            '<span style="background:{};color:{};padding:2px 8px;border-radius:9999px;'
            'font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.get_plan_display(),
        )
