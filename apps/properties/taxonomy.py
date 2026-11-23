"""
Listing category/subtype taxonomy (T-1070d, FR-LST-001, ADR-023 addendum).

`category` + `subtype` are the canonical classification. The v1 `property_type`
(residential/commercial/plot/industrial) stays as a deprecated alias that
`Property.save()` keeps in sync until the contract step (T-1070g):

    property_type → category : plot → land, others unchanged
    category → property_type : land → plot, hospitality → commercial, others unchanged

`hospitality` has no v1 value, so it projects to `commercial`; a row with
category=hospitality and property_type=commercial is consistent and is never
"repaired" back to commercial.

The drift SQL serves ``manage.py sync_listing_taxonomy``: pre-v2 pods running
during a rollout write only `property_type`, so a disagreement means
`property_type` is the newer value.
"""
from django.db import connection

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


# ── Drift repair (rolling deploys) ───────────────────────────────────────────
_DERIVED_PT = ("CASE category WHEN 'land' THEN 'plot' WHEN 'hospitality' THEN 'commercial' "
               "ELSE category END")
_DRIFT = f"category IS NULL OR {_DERIVED_PT} IS DISTINCT FROM property_type"
_FROM_PT = "CASE property_type WHEN 'plot' THEN 'land' ELSE property_type END"


def count_drift(cursor=None) -> int:
    cur = cursor or connection.cursor()
    cur.execute(f"SELECT count(*) FROM properties WHERE {_DRIFT}")
    return cur.fetchone()[0]


def sync_property_type_to_category(cursor=None, batch_size: int = 5000) -> int:
    """Derive category from property_type where they disagree (subtype is cleared)."""
    cur = cursor or connection.cursor()
    total = 0
    while True:
        cur.execute(
            f"UPDATE properties SET category = {_FROM_PT}, subtype = NULL "
            f"WHERE id IN (SELECT id FROM properties WHERE {_DRIFT} LIMIT %s)",
            [batch_size],
        )
        total += cur.rowcount
        if cur.rowcount < batch_size:
            return total


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
