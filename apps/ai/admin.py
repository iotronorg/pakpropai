from django.contrib import admin
from .models import AIInteraction, AITrace, ModelPrice, PendingAction


@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_display  = ('interaction_type', 'user', 'model_used', 'prompt_tokens',
                      'response_tokens', 'was_cached', 'response_ms', 'created_at')
    list_filter   = ('interaction_type', 'model_used', 'was_cached')
    search_fields = ('user__phone',)
    readonly_fields = ('id', 'created_at')

@admin.register(PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    """Read-only audit of confirmations. Arguments and prompt (personal data) are not shown."""
    list_display  = ('tool', 'side_effect', 'status', 'confirmed_via', 'created_at', 'confirmed_at')
    list_filter   = ('tool', 'status', 'side_effect')
    fields = readonly_fields = ('id', 'tool', 'side_effect', 'args_hash', 'status', 'confirmed_via',
                                'result', 'created_at', 'expires_at', 'confirmed_at', 'resolved_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ModelPrice)
class ModelPriceAdmin(admin.ModelAdmin):
    list_display = ('model', 'effective_from', 'currency', 'input_per_mtok', 'cached_per_mtok', 'output_per_mtok')
    list_filter = ('model',)


@admin.register(AITrace)
class AITraceAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'organization', 'route', 'intent', 'provider', 'model', 'tokens_in',
                    'tokens_out', 'cost', 'currency', 'latency_ms', 'grounding_result', 'error')
    list_filter = ('route', 'provider', 'model', 'pricing', 'grounding_result', 'fallback_used')
    list_select_related = ('organization',)
    readonly_fields = [f.name for f in AITrace._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
