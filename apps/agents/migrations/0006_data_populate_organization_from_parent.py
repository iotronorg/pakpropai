"""
Data migration: populate agent.organization from agent.parent_organization.

For each Agent that has a parent_organization (old self-FK), find the
Organization record whose admin_user matches the parent agent's user,
then set agent.organization to that Organization.

Also sets employment_type:
  - Agents with a parent_organization → INTERNAL
  - Agents without → FREELANCE (already the default, but made explicit)
"""
from django.db import migrations


def populate_organization_from_parent(apps, schema_editor):
    Agent        = apps.get_model('agents', 'Agent')
    Organization = apps.get_model('organizations', 'Organization')

    # Build a lookup: parent_agent_id → Organization
    # The Organization.admin_user is the User linked to the parent Agent.
    org_by_user = {
        org.admin_user_id: org
        for org in Organization.objects.select_related('admin_user').all()
        if org.admin_user_id is not None
    }

    agents_with_parent = Agent.objects.filter(
        parent_organization__isnull=False
    ).select_related('parent_organization__user')

    updated = []
    for agent in agents_with_parent:
        parent = agent.parent_organization
        parent_user_id = parent.user_id if parent else None
        org = org_by_user.get(parent_user_id)
        if org:
            agent.organization    = org
            agent.employment_type = 'internal'
            updated.append(agent)

    if updated:
        Agent.objects.bulk_update(updated, ['organization', 'employment_type'])


def reverse_migration(apps, schema_editor):
    Agent = apps.get_model('agents', 'Agent')
    Agent.objects.update(organization=None, employment_type='freelance')


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0005_add_employment_type_and_organization_fk'),
        ('organizations', '0002_data_migrate_from_developers'),
    ]

    operations = [
        migrations.RunPython(populate_organization_from_parent, reverse_migration),
    ]
