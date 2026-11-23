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
                       'listing_count')
    list_filter     = ('is_verified', 'level', 'market', 'source', 'is_active')
    search_fields   = ('name', 'path', 'match_key')
    readonly_fields = ('path', 'match_key', 'created_at', 'updated_at')
    autocomplete_fields = ('parent',)
    actions         = ['mark_verified', 'merge_into']

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
