"""
Backfill OrganizationMembership rows that drifted from the legacy projections (ADR-013).

Since 0010 populated the table once, these paths changed Agent.organization or
Organization.admin_user without writing a membership: agent registration, developer
team add, and admin org creation. Under use_membership_rbac (default on) those users
resolved to no organization.

Create-only and idempotent: existing rows (active or not) are left untouched; inactive
rows that disagree with a projection are reported by `manage.py reconcile_memberships`.
Reverse is a no-op: the created rows are indistinguishable from legitimate ones.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Organization = apps.get_model('organizations', 'Organization')
    Membership = apps.get_model('organizations', 'OrganizationMembership')
    Agent = apps.get_model('agents', 'Agent')

    existing = set(Membership.objects.values_list('user_id', 'organization_id'))
    to_create = []

    for org_id, admin_id in Organization.objects.filter(
        admin_user__isnull=False
    ).values_list('id', 'admin_user_id'):
        if (admin_id, org_id) not in existing:
            existing.add((admin_id, org_id))
            to_create.append(Membership(
                user_id=admin_id, organization_id=org_id,
                role='owner', employment_type='internal', is_active=True,
            ))

    for user_id, org_id, employment in Agent.objects.filter(
        user__isnull=False, organization__isnull=False
    ).values_list('user_id', 'organization_id', 'employment_type'):
        if (user_id, org_id) not in existing:
            existing.add((user_id, org_id))
            freelance = employment == 'freelance'
            to_create.append(Membership(
                user_id=user_id, organization_id=org_id,
                role='freelance_agent' if freelance else 'agent',
                employment_type='freelance' if freelance else 'internal',
                is_active=True,
            ))

    Membership.objects.bulk_create(to_create, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0023_encrypt_sensitive_fields'),
        ('agents', '0013_encrypt_sensitive_fields'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
