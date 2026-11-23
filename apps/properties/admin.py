from django.contrib import admin
from django.contrib import messages
from .models import Amenity, ListingAmenity, Property


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


class ListingAmenityInline(admin.TabularInline):
    model = ListingAmenity
    extra = 0
    autocomplete_fields = ('amenity',)


@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display  = ('key', 'group', 'markets', 'sort_order', 'is_active')
    list_filter   = ('group', 'is_active')
    search_fields = ('key',)


class ListingTransitionInline(admin.TabularInline):
    from .models import ListingTransition as model
    extra = 0
    can_delete = False
    fields = readonly_fields = ('created_at', 'from_status', 'to_status', 'source', 'actor', 'reason')
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    inlines = [ListingAmenityInline, ListingTransitionInline]

    def save_model(self, request, obj, form, change):
        from .price_history import price_change_context
        with price_change_context(user=request.user, source='admin'):
            super().save_model(request, obj, form, change)

    list_display   = ('ref_no', 'title', 'city', 'category', 'subtype', 'price', 'status', 'ai_score', 'risk_level', 'legal_status', 'owner', 'created_at')
    list_filter    = ('status', 'city', 'category', 'legal_status', 'risk_level', 'construction_status')
    search_fields  = ('ref_no', 'title', 'city', 'location', 'owner__phone')
    # Lifecycle fields change only through the actions below (audited, T-1080).
    readonly_fields = ('id', 'ref_no', 'created_at', 'updated_at', 'ai_analysis',
                       'status', 'is_active', 'status_changed_at', 'published_at', 'expires_at',
                       'expiry_reminded_at')
    ordering       = ('-ai_score', '-created_at')
    actions        = [rescore_selected, rescore_deterministic,
                      'approve_publish', 'suspend', 'reinstate_draft', 'archive']

    def _move(self, request, queryset, to, reason):
        from django.contrib import messages
        from .lifecycle import TransitionError, transition
        ok, failed = 0, []
        for prop in queryset:
            try:
                transition(prop, to, actor=request.user, reason=reason, source='admin')
                ok += 1
            except TransitionError as exc:
                failed.append(f'{prop.ref_no}: {exc}')
        if ok:
            messages.success(request, f'{ok} listing(s) moved to {to}.')
        for line in failed[:20]:
            messages.warning(request, line)

    @admin.action(description='Approve / publish selected')
    def approve_publish(self, request, queryset):
        self._move(request, queryset, 'published', 'Approved by moderator')

    @admin.action(description='Suspend selected (moderation)')
    def suspend(self, request, queryset):
        self._move(request, queryset, 'suspended', 'Suspended by moderator')

    @admin.action(description='Return selected to draft')
    def reinstate_draft(self, request, queryset):
        self._move(request, queryset, 'draft', 'Returned to draft by moderator')

    @admin.action(description='Archive selected')
    def archive(self, request, queryset):
        self._move(request, queryset, 'archived', 'Archived by moderator')
