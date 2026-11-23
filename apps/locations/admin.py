from django import forms
from django.contrib import admin, messages
from django.shortcuts import render

from .models import Location


class MergeForm(forms.Form):
    target = forms.ModelChoiceField(queryset=Location.objects.filter(is_active=True),
                                    help_text='Location to merge the selected ones into')


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Review queue: filter is_verified=No; verify, or merge into the right node."""
    list_display    = ('path', 'name', 'level', 'market', 'is_verified', 'is_active', 'source',
                       'geocode_status', 'listing_count')
    list_filter     = ('is_verified', 'level', 'market', 'source', 'is_active', 'geocode_status')
    search_fields   = ('name', 'path', 'match_key')
    readonly_fields = ('path', 'match_key', 'geocode_status', 'geocoded_at', 'geocode_place_id',
                       'geocode_note', 'created_at', 'updated_at')
    autocomplete_fields = ('parent',)
    actions         = ['mark_verified', 'merge_into', 'regeocode']

    def save_model(self, request, obj, form, change):
        # Coordinates typed by an admin are kept (never overwritten by geocoding).
        if {'latitude', 'longitude'} & set(form.changed_data):
            obj.geocode_status = Location.GeocodeStatus.MANUAL
        super().save_model(request, obj, form, change)
        if {'latitude', 'longitude'} & set(form.changed_data):
            from apps.geo.geocoding import refresh_listing_points
            refresh_listing_points(obj)

    @admin.action(description='Queue selected for (re-)geocoding')
    def regeocode(self, request, queryset):
        n = queryset.exclude(geocode_status=Location.GeocodeStatus.MANUAL).update(
            geocode_status=Location.GeocodeStatus.PENDING)
        messages.success(request, f'{n} location(s) queued; the daily geocoding job picks them up.')

    def get_queryset(self, request):
        from django.db.models import Count
        return super().get_queryset(request).annotate(_listings=Count('listings'))

    @admin.display(ordering='_listings', description='Listings')
    def listing_count(self, obj):
        return obj._listings

    @admin.action(description='Mark selected as verified')
    def mark_verified(self, request, queryset):
        n = queryset.update(is_verified=True)
        messages.success(request, f'{n} location(s) verified.')

    @admin.action(description='Merge selected into another location…')
    def merge_into(self, request, queryset):
        from .services import merge
        if 'apply' in request.POST:
            form = MergeForm(request.POST)
            if form.is_valid():
                target, moved, errors = form.cleaned_data['target'], 0, []
                for src in queryset:
                    try:
                        moved += merge(src, target)
                    except ValueError as exc:
                        errors.append(f'{src.path}: {exc}')
                for e in errors:
                    messages.error(request, e)
                messages.success(request, f'Merged into {target.path}; {moved} listing(s) moved.')
                return None
        else:
            form = MergeForm()
        return render(request, 'admin/locations/merge.html',
                      {'form': form, 'locations': queryset, 'action': 'merge_into',
                       'opts': self.model._meta})
