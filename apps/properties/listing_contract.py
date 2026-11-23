"""
Listing schema v2 contract (T-1070g, ADR-023 addendum).

Two releases:

* **g-1** (``properties 0040/0041``): the Django model stops knowing the v1
  columns ``area_marla``/``area_unit``/``area_sqm``/``property_type`` and the
  legacy ``prop_fts_gin`` index (state-only removal: the DB keeps them). A
  trigger mirrors v2 → v1 on every write, so pods of the previous release,
  which still read v1, see current values. A write that changes only v1 (an
  older pod) is copied v1 → v2 instead, so no writer's change is lost.
  0041 repairs any drift left from before the trigger (v1 wins, as in
  ``sync_listing_size``/``sync_listing_taxonomy``).
* **g-2** (staged in ``apps/properties/contract_migrations/``; moved into
  ``migrations/`` one release later): drop the trigger, the v1 columns and
  ``prop_fts_gin``. Its first operation runs :func:`problems` and refuses
  while any check fails; ``manage.py check_listing_contract`` runs the same
  checks before the deploy.

All SQL here is frozen: migrations import it, so it must not change meaning.
"""
from django.db import connection

TRIGGER = 'properties_listing_v1_compat_trg'
FUNCTION = 'properties_listing_v1_compat'

# category → v1 property_type, and back (apps.properties.taxonomy, frozen).
_PT_FROM_CATEGORY = ("CASE {c} WHEN 'land' THEN 'plot' WHEN 'hospitality' THEN 'commercial' "
                     "ELSE {c} END")
_CATEGORY_FROM_PT = "CASE {p} WHEN 'plot' THEN 'land' ELSE {p} END"

CREATE_TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger AS $$
DECLARE
    size_from_v1 boolean;
    type_from_v1 boolean;
    derived      text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        size_from_v1 := NEW.size_value IS NULL AND NEW.area_marla IS NOT NULL;
        type_from_v1 := NEW.category IS NULL AND NEW.property_type IS NOT NULL;
    ELSE
        size_from_v1 := (NEW.area_marla, NEW.area_unit) IS DISTINCT FROM (OLD.area_marla, OLD.area_unit)
                    AND (NEW.size_value, NEW.size_unit) IS NOT DISTINCT FROM (OLD.size_value, OLD.size_unit);
        type_from_v1 := NEW.property_type IS DISTINCT FROM OLD.property_type
                    AND NEW.category IS NOT DISTINCT FROM OLD.category;
    END IF;

    IF size_from_v1 THEN                     -- an older pod changed only v1
        NEW.size_value := NEW.area_marla;
        NEW.size_unit  := NEW.area_unit;
        NEW.size_m2    := NEW.area_sqm;
    ELSE                                     -- v2 is authoritative
        NEW.area_marla := NEW.size_value;
        NEW.area_unit  := COALESCE(NEW.size_unit, NEW.area_unit, 'marla');
        NEW.area_sqm   := NEW.size_m2;
    END IF;

    IF type_from_v1 THEN
        derived := {_CATEGORY_FROM_PT.format(p='NEW.property_type')};
        IF derived IS DISTINCT FROM NEW.category THEN
            NEW.subtype := NULL;             -- subtypes never span categories
        END IF;
        NEW.category := derived;
    ELSE
        NEW.category      := COALESCE(NEW.category, 'residential');
        NEW.property_type := {_PT_FROM_CATEGORY.format(c='NEW.category')};
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
CREATE TRIGGER {TRIGGER}
    BEFORE INSERT OR UPDATE OF area_marla, area_unit, area_sqm, property_type,
                               size_value, size_unit, size_m2, category
    ON properties FOR EACH ROW EXECUTE FUNCTION {FUNCTION}();
"""

DROP_TRIGGER_SQL = f"""
DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
DROP FUNCTION IF EXISTS {FUNCTION}();
"""

SIZE_DRIFT = ("size_value IS DISTINCT FROM area_marla "
              "OR size_unit IS DISTINCT FROM area_unit "
              "OR size_m2 IS DISTINCT FROM area_sqm")
TYPE_DRIFT = f"category IS NULL OR {_PT_FROM_CATEGORY.format(c='category')} IS DISTINCT FROM property_type"

VALID_CATEGORIES = ('residential', 'commercial', 'industrial', 'land', 'hospitality')
_VALID_SQL = ', '.join(f"'{c}'" for c in VALID_CATEGORIES)
INVALID_CATEGORY = f"category IS NOT NULL AND category NOT IN ({_VALID_SQL})"

# v1 property_type was never validated: the AI tool stored 'house', 'flat'…,
# which backfill 0032 copied into category. Frozen copy of
# taxonomy._TEXT_TYPES: value → (category, subtype); anything else → residential.
_TEXT_TYPES = {
    'plot': ('land', None), 'house': ('residential', 'house'), 'home': ('residential', 'house'),
    'villa': ('residential', 'villa'), 'apartment': ('residential', 'apartment'),
    'flat': ('residential', 'apartment'), 'office': ('commercial', 'office'),
    'shop': ('commercial', 'shop'), 'warehouse': ('industrial', 'warehouse'),
    'hotel': ('hospitality', 'hotel'),
}
_FIX_CATEGORY = ("CASE lower(category) " + ' '.join(
    f"WHEN '{k}' THEN '{c}'" for k, (c, _) in _TEXT_TYPES.items()) + " ELSE 'residential' END")
_FIX_SUBTYPE = ("COALESCE(subtype, CASE lower(category) " + ' '.join(
    f"WHEN '{k}' THEN '{st}'" for k, (_, st) in _TEXT_TYPES.items() if st) + " END)")

# Pre-trigger drift came from pre-v2 writers, so v1 is the newer value (ADR-023).
_REPAIRS = (
    f"UPDATE properties SET size_value = area_marla, size_unit = area_unit, size_m2 = area_sqm "
    f"WHERE id IN (SELECT id FROM properties WHERE {SIZE_DRIFT} LIMIT %s)",
    f"UPDATE properties SET category = {_CATEGORY_FROM_PT.format(p='property_type')}, subtype = NULL "
    f"WHERE id IN (SELECT id FROM properties WHERE {TYPE_DRIFT} LIMIT %s)",
    # Last: invalid categories (the trigger then re-derives property_type).
    f"UPDATE properties SET category = {_FIX_CATEGORY}, subtype = {_FIX_SUBTYPE} "
    f"WHERE id IN (SELECT id FROM properties WHERE {INVALID_CATEGORY} LIMIT %s)",
)


def repair_drift(cursor, batch_size: int = 5000) -> int:
    """Batched v1 → v2 copy for rows that disagree. Returns rows updated."""
    total = 0
    for sql in _REPAIRS:
        while True:
            cursor.execute(sql, [batch_size])
            total += cursor.rowcount
            if cursor.rowcount < batch_size:
                break
    return total


def _columns_exist(cur) -> bool:
    cur.execute("SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'properties' AND column_name IN "
                "('area_marla', 'area_unit', 'area_sqm', 'property_type')")
    return cur.fetchone()[0] == 4


def status(cursor=None) -> dict:
    """Facts the g-2 gate decides on."""
    cur = cursor or connection.cursor()
    cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal", [TRIGGER])
    trigger = cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM django_migrations WHERE app = 'properties' AND name = %s",
                ['0041_repair_v1_drift'])
    repaired = cur.fetchone()[0] == 1
    columns = _columns_exist(cur)
    cur.execute(f"SELECT count(*) FROM properties WHERE {INVALID_CATEGORY} OR category IS NULL")
    invalid = cur.fetchone()[0]
    out = {'trigger': trigger, 'g1_applied': repaired, 'v1_columns': columns,
           'size_drift': None, 'type_drift': None, 'invalid_category': invalid}
    if columns:
        cur.execute(f"SELECT count(*) FROM properties WHERE {SIZE_DRIFT}")
        out['size_drift'] = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM properties WHERE {TYPE_DRIFT}")
        out['type_drift'] = cur.fetchone()[0]
    return out


def problems(cursor=None) -> list[str]:
    """Reasons the v1 columns cannot be dropped yet; [] = safe."""
    s = status(cursor)
    if not s['v1_columns']:
        return []                                # already dropped: nothing to guard
    out = []
    if not s['g1_applied']:
        out.append('properties 0041_repair_v1_drift (T-1070g-1) is not applied: '
                   'deploy g-1 and let it run for one release first.')
    if not s['trigger']:
        out.append(f'trigger {TRIGGER} is missing: v1 columns may be stale.')
    if s['size_drift']:
        out.append(f"{s['size_drift']} listing(s) where size_* and area_* disagree.")
    if s['type_drift']:
        out.append(f"{s['type_drift']} listing(s) where category and property_type disagree.")
    if s['invalid_category']:
        out.append(f"{s['invalid_category']} listing(s) with a missing or unknown category.")
    return out
