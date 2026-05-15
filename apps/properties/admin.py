from django.contrib import admin
from django.contrib import messages
from .models import Property


def rescore_selected(modeladmin, request, queryset):
    from .tasks import score_property_task
    count = 0
    for prop in queryset:
        score_property_task.delay(str(prop.id))
        count += 1
    messages.success(request, f"Queued rescore for {count} propert{'y' if count == 1 else 'ies'}.")

rescore_selected.short_description = "Re-score selected properties (async)"


def rescore_deterministic(modeladmin, request, queryset):
    from .scoring import PropertyScoringEngine
    count = 0
    for prop in queryset.prefetch_related('verifications__document_scans'):
        signals         = PropertyScoringEngine.compute_signals(prop)
        prop.ai_score   = PropertyScoringEngine.deterministic_score(signals)
        prop.risk_level = PropertyScoringEngine.risk_from_score(prop.ai_score)
        prop.ai_analysis = {
            'score':   prop.ai_score,
            'risk':    prop.risk_level,
            'factors': [],
            'signals': signals,
            'source':  'deterministic',
        }
        prop.save(update_fields=['ai_score', 'risk_level', 'ai_analysis'])
        count += 1
    messages.success(request, f"Deterministically scored {count} propert{'y' if count == 1 else 'ies'}.")

rescore_deterministic.short_description = "Re-score selected (deterministic, instant)"


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display   = ('ref_no', 'title', 'city', 'property_type', 'price_pkr', 'ai_score', 'risk_level', 'legal_status', 'owner', 'created_at')
    list_filter    = ('city', 'property_type', 'legal_status', 'risk_level', 'construction_status')
    search_fields  = ('ref_no', 'title', 'city', 'location', 'owner__phone')
    readonly_fields = ('id', 'ref_no', 'created_at', 'updated_at', 'ai_analysis')
    ordering       = ('-ai_score', '-created_at')
    actions        = [rescore_selected, rescore_deterministic]
