"""
Baseline price-history row per priced listing (T-1070c), dated at the listing's
creation, so the first real change has something to compare against.
Idempotent (skips listings that already have history); reverse deletes only
the backfill rows.
"""
from django.db import migrations

FORWARD = """
INSERT INTO property_price_history
    (property_id, price, currency, price_type, price_on_request, changed_at, source)
SELECT p.id, p.price, p.currency, p.price_type, p.price_on_request, p.created_at, 'backfill'
FROM properties p
WHERE (p.price IS NOT NULL OR p.price_on_request)
  AND NOT EXISTS (SELECT 1 FROM property_price_history h WHERE h.property_id = p.id)
"""
REVERSE = "DELETE FROM property_price_history WHERE source = 'backfill'"


class Migration(migrations.Migration):
    dependencies = [
        ('properties', '0029_price_history'),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
