"""
Listing category/subtype taxonomy (T-1070d, FR-LST-001, ADR-023 addendum).

`category` + `subtype` are the canonical classification. The v1 `property_type`
(residential/commercial/plot/industrial) is a derived, read-only value since
T-1070g-1 (``Property.property_type``; the API alias; a DB trigger mirrors the
legacy column for older pods until g-2 drops it):

    property_type → category : plot → land, others unchanged
    category → property_type : land → plot, hospitality → commercial, others unchanged

`hospitality` has no v1 value, so it projects to `commercial`; a row with
category=hospitality and property_type=commercial is consistent and is never
"repaired" back to commercial.
"""

CATEGORIES = ('residential', 'commercial', 'industrial', 'land', 'hospitality')

CATEGORY_LABELS = {
    'residential': 'Residential',
    'commercial':  'Commercial',
    'industrial':  'Industrial',
    'land':        'Land',
    'hospitality': 'Hospitality',
}

# Market-neutral subtypes. Market-specific nuances (e.g. PK "upper portion")
# are subtypes too: they are harmless elsewhere and keep the list flat.
SUBTYPES: dict[str, tuple[str, ...]] = {
    'residential': ('house', 'apartment', 'upper_portion', 'lower_portion', 'villa',
                    'townhouse', 'penthouse', 'farmhouse', 'studio', 'room'),
    'commercial':  ('office', 'shop', 'showroom', 'building', 'mixed_use'),
    'industrial':  ('warehouse', 'factory', 'workshop', 'cold_storage'),
    'land':        ('residential_plot', 'commercial_plot', 'industrial_plot',
                    'agricultural_land', 'plot_file'),
    'hospitality': ('hotel', 'guest_house', 'serviced_apartment', 'resort'),
}

ALL_SUBTYPES = frozenset(s for subs in SUBTYPES.values() for s in subs)


def subtype_label(subtype: str) -> str:
    return subtype.replace('_', ' ').capitalize()


_PT_TO_CATEGORY = {'plot': 'land'}
_CATEGORY_TO_PT = {'land': 'plot', 'hospitality': 'commercial'}


def category_for_property_type(property_type: str | None) -> str | None:
    if not property_type:
        return None
    return _PT_TO_CATEGORY.get(property_type, property_type)


def property_type_for_category(category: str | None) -> str | None:
    if not category:
        return None
    return _CATEGORY_TO_PT.get(category, category)


def subtype_errors(category: str | None, subtype: str | None) -> dict:
    """Validation shared by Model.clean (via v2_errors) and the API."""
    if not subtype:
        return {}
    if not category:
        return {'subtype': 'Requires a category.'}
    allowed = SUBTYPES.get(category, ())
    if subtype not in allowed:
        return {'subtype': f"Not a {category} subtype. Allowed: {', '.join(allowed)}."}
    return {}


# ── Legacy property_type vocabulary (no column since T-1070g-1) ──────────────

def categories_for_property_type(property_type: str | None) -> list[str]:
    """Categories a legacy ``?type=`` value covers (commercial → commercial + hospitality)."""
    if not property_type:
        return []
    return [c for c in CATEGORIES if property_type_for_category(c) == property_type]


def property_type_expression():
    """ORM expression deriving the legacy type from ``category`` (for values()/grouping)."""
    from django.db.models import CharField, F, Value, When, Case
    return Case(
        *(When(category=c, then=Value(pt)) for c, pt in _CATEGORY_TO_PT.items()),
        When(category__isnull=True, then=Value('residential')),
        default=F('category'), output_field=CharField(),
    )


_TEXT_TYPES = {
    'plot': ('land', None), 'land': ('land', None), 'residential': ('residential', None),
    'commercial': ('commercial', None), 'industrial': ('industrial', None),
    'hospitality': ('hospitality', None), 'house': ('residential', 'house'),
    'home': ('residential', 'house'), 'villa': ('residential', 'villa'),
    'apartment': ('residential', 'apartment'), 'flat': ('residential', 'apartment'),
    'office': ('commercial', 'office'), 'shop': ('commercial', 'shop'),
    'warehouse': ('industrial', 'warehouse'), 'hotel': ('hospitality', 'hotel'),
}


def classify_type_text(text: str | None) -> tuple[str, str | None]:
    """Free-text type from the AI tool ('plot', 'house', 'flat'…) → (category, subtype)."""
    return _TEXT_TYPES.get((text or '').strip().lower(), ('residential', None))


# ── Amenity filters (API list filter + search service) ───────────────────────

def parse_amenity_keys(value) -> list[str]:
    """`'gym, Pool'` or `['gym', 'pool']` → sorted unique lower-case keys."""
    if not value:
        return []
    items = value.split(',') if isinstance(value, str) else value
    return sorted({str(k).strip().lower() for k in items if str(k).strip()})


def active_amenity_keys(keys) -> set[str]:
    from .models import Amenity
    return set(Amenity.objects.filter(key__in=list(keys), is_active=True)
               .values_list('key', flat=True))


def filter_all_amenities(qs, keys):
    """Listings that have every amenity in ``keys`` (one grouped subquery, no join fan-out)."""
    from django.db.models import Count
    from .models import ListingAmenity
    keys = set(keys)
    having_all = (ListingAmenity.objects.filter(amenity__key__in=keys)
                  .values('property').annotate(n=Count('amenity', distinct=True))
                  .filter(n=len(keys)).values('property'))
    return qs.filter(pk__in=having_all)
