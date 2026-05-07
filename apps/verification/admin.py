from django.contrib import admin
from .models import Verification, DocumentScan


@admin.register(Verification)
class VerificationAdmin(admin.ModelAdmin):
    list_display    = ('property', 'requested_by', 'status', 'verified_at', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('property__title', 'requested_by__phone')
    readonly_fields = ('id', 'created_at', 'verified_at')


@admin.register(DocumentScan)
class DocumentScanAdmin(admin.ModelAdmin):
    list_display    = ('document_type', 'owner_name', 'phone', 'status', 'confidence', 'created_at')
    list_filter     = ('document_type', 'status', 'confidence')
    search_fields   = ('owner_name', 'phone', 'cnic_number', 'property_address')
    readonly_fields = ('created_at', 'raw_ocr', 'extracted_fields', 'red_flags', 'whatsapp_summary')