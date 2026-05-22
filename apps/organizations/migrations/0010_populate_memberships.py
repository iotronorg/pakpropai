"""
Data migration: populate OrganizationMembership from existing associations.

Sources:
  1. Organization.admin_user  → role='owner',  employment_type='internal'
  2. Agent.user + Agent.organization → role='agent', employment_type=agent.employment_type
"""
from django.db import migrations


def populate_memberships(apps, schema_editor):
    Organization = apps.get_model('organizations', 'Organization')
    OrganizationMembership = apps.get_model('organizations', 'OrganizationMembership')
    Agent = apps.get_model('agents', 'Agent')

    created = 0

    # Org owners
    for org in Organization.objects.select_related('admin_user').filter(admin_user__isnull=False):
        _, added = OrganizationMembership.objects.get_or_create(
            user=org.admin_user,
            organization=org,
            defaults={'role': 'owner', 'employment_type': 'internal', 'is_active': True},
        )
        if added:
            created += 1

    # Agents with a linked user account and an organization
    for agent in Agent.objects.select_related('user', 'organization').filter(
        user__isnull=False, organization__isnull=False
    ):
        _, added = OrganizationMembership.objects.get_or_create(
            user=agent.user,
            organization=agent.organization,
            defaults={
                'role': 'agent',
                'employment_type': agent.employment_type or 'internal',
                'is_active': True,
            },
        )
        if added:
            created += 1


def reverse_memberships(apps, schema_editor):
    OrganizationMembership = apps.get_model('organizations', 'OrganizationMembership')
    OrganizationMembership.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0009_organization_membership'),
        ('agents', '0005_add_employment_type_and_organization_fk'),
    ]

    operations = [
        migrations.RunPython(populate_memberships, reverse_memberships),
    ]
