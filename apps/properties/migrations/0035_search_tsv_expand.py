"""
Stored full-text document (T-1070f, KI-035, ADR-015 addendum).

A nullable tsvector column kept current by a BEFORE trigger, so every writer
(this code, pre-v2 pods during a rollout, QuerySet.update, raw SQL) keeps it
right without app changes. The document matches text_search.search_vector():
the `simple` config over title, city, location, description and ref_no.
The trigger also fires when search_tsv itself is written, so a stale value from
a full model save() is recomputed. Rolling-safe: additive column, no rewrite.
"""
import django.contrib.postgres.search
from django.db import migrations

DOCUMENT = ("to_tsvector('simple'::regconfig, "
            "COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.city, '') || ' ' || "
            "COALESCE(NEW.location, '') || ' ' || COALESCE(NEW.description, '') || ' ' || "
            "COALESCE(NEW.ref_no, ''))")

FORWARD = f"""
CREATE OR REPLACE FUNCTION properties_search_tsv_refresh() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv := {DOCUMENT};
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS properties_search_tsv_trg ON properties;
CREATE TRIGGER properties_search_tsv_trg
    BEFORE INSERT OR UPDATE OF title, city, location, description, ref_no, search_tsv
    ON properties FOR EACH ROW EXECUTE FUNCTION properties_search_tsv_refresh();
"""

REVERSE = """
DROP TRIGGER IF EXISTS properties_search_tsv_trg ON properties;
DROP FUNCTION IF EXISTS properties_search_tsv_refresh();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0034_property_category_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='search_tsv',
            field=django.contrib.postgres.search.SearchVectorField(editable=False, null=True),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
