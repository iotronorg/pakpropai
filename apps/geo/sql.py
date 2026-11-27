"""
Frozen SQL for the listing public point (T-1060b, ADR-024 addendum).
Migrations import these strings, so they must not change meaning.
"""
TRIGGER = 'properties_geo_point_trg'
FUNCTION = 'properties_geo_point'
SNAP_DECIMALS = 2                         # ~1.1 km of latitude

# Exact only for an organisation/agent/platform listing marked exact.
_MASKED = "NEW.listing_owner_type = 'client' OR NEW.location_precision IS DISTINCT FROM 'exact'"

CREATE_TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger AS $$
BEGIN
    IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
        NEW.geo_lat := NULL;
        NEW.geo_lng := NULL;
    ELSIF {_MASKED} THEN
        NEW.geo_lat := round(NEW.latitude, {SNAP_DECIMALS});
        NEW.geo_lng := round(NEW.longitude, {SNAP_DECIMALS});
    ELSE
        NEW.geo_lat := NEW.latitude;
        NEW.geo_lng := NEW.longitude;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
CREATE TRIGGER {TRIGGER}
    BEFORE INSERT OR UPDATE OF latitude, longitude, listing_owner_type, location_precision,
                               geo_lat, geo_lng
    ON properties FOR EACH ROW EXECUTE FUNCTION {FUNCTION}();
"""

DROP_TRIGGER_SQL = f"""
DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
DROP FUNCTION IF EXISTS {FUNCTION}();
"""

# PostGIS (optional; created by `manage.py geo_setup`, not by a migration).
GIST_INDEX = 'prop_geo_point_gist'
GEOG_EXPR = ('(ST_SetSRID(ST_MakePoint("properties"."geo_lng"::float8, '
             '"properties"."geo_lat"::float8), 4326)::geography)')
GIST_INDEX_SQL = ('CREATE INDEX {concurrently} IF NOT EXISTS ' + GIST_INDEX + ' ON properties '
                  'USING gist ((ST_SetSRID(ST_MakePoint(geo_lng::float8, geo_lat::float8), 4326)'
                  '::geography))')

# ── v2 (T-1060c, geo 0001): a listing without its own coordinates falls back to
# its location's centroid, always snapped (it is approximate by nature). Same
# function name: geo 0001 replaces v1, its reverse restores v1.
CREATE_TRIGGER_SQL_V2 = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger AS $$
DECLARE
    c_lat numeric;
    c_lng numeric;
BEGIN
    IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
        c_lat := NULL;
        c_lng := NULL;
        IF NEW.location_ref_id IS NOT NULL THEN
            SELECT l.latitude, l.longitude INTO c_lat, c_lng
              FROM locations l WHERE l.id = NEW.location_ref_id;
        END IF;
        IF c_lat IS NULL OR c_lng IS NULL THEN
            NEW.geo_lat := NULL;
            NEW.geo_lng := NULL;
        ELSE
            NEW.geo_lat := round(c_lat, {SNAP_DECIMALS});
            NEW.geo_lng := round(c_lng, {SNAP_DECIMALS});
        END IF;
    ELSIF {_MASKED} THEN
        NEW.geo_lat := round(NEW.latitude, {SNAP_DECIMALS});
        NEW.geo_lng := round(NEW.longitude, {SNAP_DECIMALS});
    ELSE
        NEW.geo_lat := NEW.latitude;
        NEW.geo_lng := NEW.longitude;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
CREATE TRIGGER {TRIGGER}
    BEFORE INSERT OR UPDATE OF latitude, longitude, listing_owner_type, location_precision,
                               location_ref_id, geo_lat, geo_lng
    ON properties FOR EACH ROW EXECUTE FUNCTION {FUNCTION}();
"""  # nosec B608 - only constants or quoted identifiers are interpolated; values are parameters
