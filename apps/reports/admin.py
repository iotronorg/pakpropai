from django.contrib import admin
from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display  = ('user', 'report_type', 'status', 'property', 'created_at', 'ready_at')
    list_filter   = ('report_type', 'status')
    search_fields = ('user__phone',)
    readonly_fields = ('id', 'created_at', 'ready_at')