"""
Data migration: create Organization records for existing developer users.

For every User with role='developer' that has a linked Agent profile,
an Organization is created using the agent's company/profile data.
Users without an Agent profile get a minimal Organization from user data.
The Organization.admin_user is set to the developer's User account.
"""
from django.db import migrations
from django.utils.text import slugify


def _unique_slug(Organization, base):
    slug = base[:120]
    n = 1
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{base[:116]}-{n}"
        n += 1
    return slug


def create_orgs_from_developers(apps, schema_editor):
    User         = apps.get_model('users', 'User')
    Agent        = apps.get_model('agents', 'Agent')
    Organization = apps.get_model('organizations', 'Organization')

    for user in User.objects.filter(role='developer'):
        # Skip if an Organization already exists for this user
        if Organization.objects.filter(admin_user=user).exists():
            continue

        # Try to get the linked Agent profile
        try:
            agent = Agent.objects.get(user=user)
        except Agent.DoesNotExist:
            agent = None

        if agent:
            # Determine org_type from agent_type
            org_type_map = {
                'developer': 'developer',
                'agency':    'agency',
                'individual': 'agency',  # independent agents become agencies
            }
            org_type = org_type_map.get(agent.agent_type, 'agency')
            name  = agent.company_name or agent.name or user.name or user.phone
            phone = agent.phone or user.phone
            email = agent.email or user.email
            city  = agent.primary_city or (agent.cities[0] if agent.cities else '')
        else:
            org_type = 'developer'
            name  = user.name or user.phone
            phone = user.phone
            email = user.email
            city  = ''

        base_slug = slugify(name) or slugify(user.phone)
        slug = _unique_slug(Organization, base_slug)

        Organization.objects.create(
            name       = name,
            slug       = slug,
            org_type   = org_type,
            admin_user = user,
            phone      = phone,
            email      = email,
            city       = city,
            country    = 'PK',
            is_active  = user.is_active,
            is_verified = getattr(agent, 'is_verified', False) if agent else False,
        )


def reverse_migration(apps, schema_editor):
    # Reversing removes only the orgs created for developer users
    Organization = apps.get_model('organizations', 'Organization')
    User         = apps.get_model('users', 'User')
    developer_ids = User.objects.filter(role='developer').values_list('id', flat=True)
    Organization.objects.filter(admin_user_id__in=developer_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_initial'),
        ('agents', '0004_agent_availability'),
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_orgs_from_developers, reverse_migration),
    ]
