"""
T-1032b (ADR-013 phase C, release 1 of 2 — expand).

1. ``AgentOrgMembership.membership`` (nullable FK to the canonical
   ``OrganizationMembership``), backfilled from (agent.user, organization).
   ``agent_org_memberships`` is admin-written and small; one UPDATE.
2. ``AgentOrganizationMembership`` leaves the model **state**. Its table
   ``agent_organization_memberships`` (and any rows) stays until the staged
   contract migration (apps/agents/contract_migrations/) drops it once
   ``reconcile_memberships`` reports it empty. Its two FK constraints are
   dropped now: Django no longer knows the table, so a TRUNCATE of
   ``organizations`` / ``freelance_agent_profiles`` (test flushes) would fail
   on them, and the table has no writers.

Reverse: re-adds the FK constraints (NOT VALID, then VALIDATE), drops the link
column; the legacy model returns to the state.
"""
import django.db.models.deletion
from django.db import migrations, models

BACKFILL_SQL = """
UPDATE agent_org_memberships c
   SET membership_id = m.id
  FROM agents a, organization_memberships m
 WHERE c.membership_id IS NULL
   AND a.id = c.agent_id
   AND m.user_id = a.user_id
   AND m.organization_id = c.organization_id;
"""

DROP_LEGACY_FKS_SQL = """
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = to_regclass('agent_organization_memberships') AND contype = 'f'
  LOOP
    EXECUTE format('ALTER TABLE agent_organization_memberships DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;
"""

RESTORE_LEGACY_FKS_SQL = """
ALTER TABLE agent_organization_memberships
  ADD CONSTRAINT agent_org_memberships_legacy_fa_fk FOREIGN KEY (freelance_agent_id)
      REFERENCES freelance_agent_profiles (id) DEFERRABLE INITIALLY DEFERRED NOT VALID,
  ADD CONSTRAINT agent_org_memberships_legacy_org_fk FOREIGN KEY (organization_id)
      REFERENCES organizations (id) DEFERRABLE INITIALLY DEFERRED NOT VALID;
ALTER TABLE agent_organization_memberships VALIDATE CONSTRAINT agent_org_memberships_legacy_fa_fk;
ALTER TABLE agent_organization_memberships VALIDATE CONSTRAINT agent_org_memberships_legacy_org_fk;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0014_image_hardening'),
        ('organizations', '0028_owner_projection_help'),
    ]

    operations = [
        migrations.AddField(
            model_name='agentorgmembership',
            name='membership',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='collaboration_terms',
                                    to='organizations.organizationmembership'),
        ),
        migrations.RunSQL(BACKFILL_SQL, migrations.RunSQL.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name='AgentOrganizationMembership')],
            database_operations=[migrations.RunSQL(DROP_LEGACY_FKS_SQL, RESTORE_LEGACY_FKS_SQL)],
        ),
    ]
