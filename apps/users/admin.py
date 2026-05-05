from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User, OTPCode


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ('-created_at',)
    list_display  = ('phone', 'name', 'role', 'is_filer', 'is_active', 'last_active', 'created_at')
    list_filter   = ('role', 'is_filer', 'is_active')
    search_fields = ('phone', 'name', 'email', 'ntn', 'cnic')
    readonly_fields = ('id', 'created_at', 'last_active', 'last_login')
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
    list_display  = ('phone', 'code', 'is_used', 'attempts', 'expires_at', 'created_at')
    list_filter   = ('is_used',)
    search_fields = ('phone',)
    readonly_fields = ('created_at',)