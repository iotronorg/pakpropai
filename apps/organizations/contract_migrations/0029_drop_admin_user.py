"""
T-1032b-2 (ADR-013 phase C, release 2 of 2) — STAGED. See README.md here.

Ships in the release AFTER the one with ``organizations 0028`` / ``agents 0015``
(T-1032b-1), **together with the code changes listed in README.md** (the
``admin_user`` model field, its post_save hook and projection writer go away in
the same release; this migration removes the field from the model state).

1. Gate: refuses unless ``find_drift()`` is clean for every CONTRACT_BLOCKERS
   category (same as ``reconcile_memberships --fail-on-drift`` for those).
2. Drops ``organizations.admin_user_id`` (unique constraint and FK go with it).

Reverse (emergency rollback to a b-1 build): re-adds the nullable one-to-one
column and refills it from owner memberships (each user projected into their
oldest owned org only, as b-1 does).
"""
from django.db import migrations


def gate(apps, schema_editor):
    from apps.organizations.membership import CONTRACT_BLOCKERS, find_drift
    drift = find_drift()
    found = [f'{k}: {len(drift[k])}' for k in CONTRACT_BLOCKERS if drift.get(k)]
    if found:
        raise RuntimeError('T-1032b-2 refused; run `manage.py reconcile_memberships` '
                           '(--apply fixes the repairable ones):\n- ' + '\n- '.join(found))


DROP_SQL = """
SET LOCAL lock_timeout = '5s';
ALTER TABLE organizations DROP COLUMN IF EXISTS admin_user_id;
"""

RESTORE_SQL = """
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS admin_user_id uuid NULL UNIQUE
        REFERENCES users (id) DEFERRABLE INITIALLY DEFERRED;
UPDATE organizations o SET admin_user_id = first_owned.user_id
  FROM (SELECT DISTINCT ON (m.user_id) m.user_id, m.organization_id
          FROM organization_memberships m
         WHERE m.role = 'owner' AND m.is_active
         ORDER BY m.user_id, m.joined_at, m.id) first_owned
 WHERE o.id = first_owned.organization_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0028_owner_projection_help'),
        ('agents', '0016_encrypt_more_fields'),
    ]

    operations = [
        migrations.RunPython(gate, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.RemoveField(model_name='organization', name='admin_user')],
            database_operations=[migrations.RunSQL(DROP_SQL, RESTORE_SQL)],
        ),
    ]
