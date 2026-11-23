"""
Listing status ↔ is_active trigger (T-1080, properties 0045). Frozen: migrations import it.

``status`` is the source of truth; ``is_active`` = status IN (published,
under_offer). Writers that only know ``is_active`` (pods of the previous
release, raw SQL) still work: on INSERT a visible default status with
is_active = false becomes 'archived'; on UPDATE a change to is_active alone
maps to published / archived. Property.save() mirrors this (_sync_status).
"""
TRIGGER = 'properties_status_trg'
FUNCTION = 'properties_status_sync'
VISIBLE_SQL = "('published', 'under_offer')"
STATUSES = ('draft', 'pending_review', 'published', 'under_offer', 'sold', 'let',
            'expired', 'archived', 'suspended')
CONSTRAINT = 'property_status_valid'

CREATE_TRIGGER_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IN {VISIBLE_SQL} AND NOT NEW.is_active THEN
            NEW.status := 'archived';
        END IF;
        NEW.status_changed_at := COALESCE(NEW.status_changed_at, now());
    ELSE
        IF NEW.status IS NOT DISTINCT FROM OLD.status
           AND NEW.is_active IS DISTINCT FROM OLD.is_active THEN
            IF NEW.is_active AND NEW.status NOT IN {VISIBLE_SQL} THEN
                NEW.status := 'published';
            ELSIF NOT NEW.is_active AND NEW.status IN {VISIBLE_SQL} THEN
                NEW.status := 'archived';
            END IF;
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status
           AND NEW.status_changed_at IS NOT DISTINCT FROM OLD.status_changed_at THEN
            NEW.status_changed_at := now();
        END IF;
    END IF;
    NEW.is_active := NEW.status IN {VISIBLE_SQL};
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
CREATE TRIGGER {TRIGGER}
    BEFORE INSERT OR UPDATE OF status, is_active
    ON properties FOR EACH ROW EXECUTE FUNCTION {FUNCTION}();
"""

DROP_TRIGGER_SQL = f"""
DROP TRIGGER IF EXISTS {TRIGGER} ON properties;
DROP FUNCTION IF EXISTS {FUNCTION}();
"""

_IN = ', '.join(f"'{s}'" for s in STATUSES)
ADD_CONSTRAINT_SQL = (f"ALTER TABLE properties ADD CONSTRAINT {CONSTRAINT} "
                      f"CHECK (status IN ({_IN})) NOT VALID; "
                      f"ALTER TABLE properties VALIDATE CONSTRAINT {CONSTRAINT};")
DROP_CONSTRAINT_SQL = f"ALTER TABLE properties DROP CONSTRAINT IF EXISTS {CONSTRAINT};"
