

# Register your models here.
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display  = ('phone', 'name', 'role', 'is_filer', 'is_active', 'created_at')
    list_filter   = ('role', 'is_active', 'is_filer')
    search_fields = ('phone', 'name', 'cnic', 'ntn')
    ordering      = ('-created_at',)

    fieldsets = (
        (None,           {'fields': ('phone', 'password')}),
        ('Personal info', {'fields': ('name', 'cnic', 'ntn')}),
        ('Role & Tax',   {'fields': ('role', 'is_filer')}),
        ('Permissions',  {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Timestamps',   {'fields': ('created_at', 'last_active')}),
    )
    readonly_fields   = ('created_at', 'last_active')
    add_fieldsets     = (
        (None, {
            'classes': ('wide',),
            'fields':  ('phone', 'name', 'role', 'password1', 'password2'),
        }),
    )