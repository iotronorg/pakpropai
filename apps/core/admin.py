from django.contrib import admin

from .models import BackupRun


@admin.register(BackupRun)
class BackupRunAdmin(admin.ModelAdmin):
    """Read-only: rows are written by ``manage.py record_backup_run`` from the host scripts (T-6010)."""
    list_display = ('finished_at', 'kind', 'status', 'file_name', 'size_bytes', 'host')
    list_filter = ('kind', 'status', 'host')
    search_fields = ('file_name', 'detail')
    ordering = ('-finished_at',)
    readonly_fields = [f.name for f in BackupRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
