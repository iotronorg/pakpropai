"""
T-1040 (KI-017): ``is_sandbox`` follows the organization; leads with no
organization are live. Backfill (batched, non-atomic, idempotent):

* organization IS NULL and is_sandbox → False
* organization in production and is_sandbox → False (leads that got their org
  after insert kept the default; 0019 only covered rows existing then)

Leads of non-production orgs are not touched. Reverse is a no-op (the flag is
derived and nothing filters leads on it).
"""
from django.db import migrations

BATCH = 5000

SELECT_SQL = """
SELECT l.id FROM leads l
LEFT JOIN organizations o ON o.id = l.organization_id
WHERE l.is_sandbox AND (l.organization_id IS NULL OR o.operational_mode = 'production')
ORDER BY l.id LIMIT %s
"""


def backfill(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        while True:
            cur.execute(SELECT_SQL, [BATCH])
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                break
            cur.execute('UPDATE leads SET is_sandbox = FALSE WHERE id = ANY(%s)', [ids])


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('leads', '0020_alter_lead_budget_currency'),
        ('organizations', '0028_owner_projection_help'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
