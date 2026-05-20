from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0007_remove_parent_organization'),
        ('properties', '0010_remove_property_prop_org_idx_alter_property_ref_no'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='listing_owner_type',
            field=models.CharField(
                choices=[
                    ('organization',    'Organization'),
                    ('freelance_agent', 'Freelance Agent'),
                    ('client',          'Client / Individual'),
                    ('platform',        'Platform (Demo/Internal)'),
                ],
                db_index=True,
                default='organization',
                help_text='Discriminator: who owns this listing — enforced by DB constraint',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='property',
            name='listed_by_agent',
            field=models.ForeignKey(
                blank=True,
                help_text='Set when listing_owner_type=freelance_agent',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='freelance_listings',
                to='agents.agent',
            ),
        ),
        migrations.AddField(
            model_name='property',
            name='area_unit',
            field=models.CharField(
                choices=[
                    ('marla',  'Marla'),
                    ('kanal',  'Kanal'),
                    ('sqft',   'Square Feet'),
                    ('sqm',    'Square Metre'),
                    ('acre',   'Acre'),
                    ('guntha', 'Guntha'),
                    ('cent',   'Cent'),
                ],
                default='marla',
                help_text='Unit for area_marla — marla, kanal, sqft, sqm, acre, etc.',
                max_length=10,
            ),
        ),
        migrations.RunPython(
            code=lambda apps, schema_editor: apps.get_model('properties', 'Property').objects.filter(
                organization__isnull=True, owner__isnull=False
            ).update(listing_owner_type='client') or
            apps.get_model('properties', 'Property').objects.filter(
                organization__isnull=True, owner__isnull=True
            ).update(listing_owner_type='platform'),
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['listing_owner_type'], name='prop_owner_type_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['organization', 'is_active'], name='prop_org_active_idx'),
        ),
        migrations.AddConstraint(
            model_name='property',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(listing_owner_type='organization',    organization__isnull=False) |
                    models.Q(listing_owner_type='freelance_agent', listed_by_agent__isnull=False) |
                    models.Q(listing_owner_type='client',          owner__isnull=False) |
                    models.Q(listing_owner_type='platform')
                ),
                name='property_ownership_consistency',
            ),
        ),
    ]
