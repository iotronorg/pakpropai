from django.contrib import admin
from .models import AIInteraction


@admin.register(AIInteraction)
class AIInteractionAdmin(admin.ModelAdmin):
    list_display  = ('interaction_type', 'user', 'model_used', 'prompt_tokens',
                      'response_tokens', 'was_cached', 'response_ms', 'created_at')
    list_filter   = ('interaction_type', 'model_used', 'was_cached')
    search_fields = ('user__phone',)
    readonly_fields = ('id', 'created_at')