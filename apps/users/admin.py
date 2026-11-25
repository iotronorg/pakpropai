from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User, OTPCode


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ('-created_at',)
    list_display  = ('phone', 'name', 'role', 'is_filer', 'is_active', 'mfa_enabled', 'last_active', 'created_at')
    list_filter   = ('role', 'is_filer', 'is_active', 'mfa_enabled')
    actions       = ['reset_mfa']
    search_fields = ('phone', 'name', 'email')
    readonly_fields = ('id', 'created_at', 'last_active', 'last_login', 'mfa_enabled')

    @admin.action(description='Reset MFA (user re-enrols; sessions ended; audited)')
    def reset_mfa(self, request, queryset):
        from django.contrib import messages
        from .mfa import disable
        n = 0
        for user in queryset.filter(mfa_enabled=True):
            disable(user, by=request.user, reason='admin action')
            n += 1
        messages.success(request, f'MFA reset for {n} user(s).')
    fieldsets = (
        (None,           {'fields': ('id', 'phone', 'password')}),
        ('Personal',     {'fields': ('name', 'email', 'cnic', 'ntn', 'is_filer')}),
        ('Permissions',  {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates',        {'fields': ('last_login', 'last_active', 'created_at')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',), 'fields': ('phone', 'password1', 'password2')}),
    )


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display  = ('phone', 'purpose', 'is_used', 'attempts', 'expires_at', 'created_at')
    list_filter   = ('is_used',)
    search_fields = ('phone',)
    readonly_fields = ('created_at',)