from django.db import migrations


def backfill_super_admin(apps, schema_editor):
    User = apps.get_model('users', 'User')
    User.objects.filter(role='admin', platform_role__isnull=True).update(
        platform_role='super_admin'
    )


def reverse_backfill(apps, schema_editor):
    User = apps.get_model('users', 'User')
    User.objects.filter(role='admin', platform_role='super_admin').update(
        platform_role=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_user_platform_role'),
    ]

    operations = [
        migrations.RunPython(backfill_super_admin, reverse_backfill),
    ]
