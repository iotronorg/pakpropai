"""
T-1080 expand (ADR-025): listing lifecycle.

Adds ``status`` (DB default 'published', so inserts from the previous release
still work), the lifecycle timestamps and the ``property_transitions`` audit
table, plus the status ↔ is_active trigger (apps/properties/lifecycle_sql.py).
The status CHECK is added NOT VALID here (no scan under the ALTER lock) and
validated in 0046 after the backfill. Indexes: 0047 (concurrently).
"""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

from apps.properties.lifecycle_sql import (CONSTRAINT, CREATE_TRIGGER_SQL, DROP_CONSTRAINT_SQL,
                                           DROP_TRIGGER_SQL, STATUSES)

STATUS_CHOICES = [('draft', 'Draft'), ('pending_review', 'Pending review'), ('published', 'Published'),
                  ('under_offer', 'Under offer'), ('sold', 'Sold'), ('let', 'Let'),
                  ('expired', 'Expired'), ('archived', 'Archived'), ('suspended', 'Suspended')]
_IN = ', '.join(f"'{s}'" for s in STATUSES)


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0044_geo_point_index'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField('property', 'status', models.CharField(
            choices=STATUS_CHOICES, db_default='published', default='published', max_length=20)),
        migrations.AddField('property', 'status_changed_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('property', 'published_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('property', 'expires_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('property', 'expiry_reminded_at', models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(
            name='ListingTransition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_status', models.CharField(blank=True, max_length=20)),
                ('to_status', models.CharField(choices=STATUS_CHOICES, max_length=20)),
                ('source', models.CharField(choices=[('api', 'API'), ('admin', 'Admin'), ('system', 'System (expiry)'),
                                                     ('create', 'Created'), ('direct', 'Direct write (legacy path)')],
                                            default='direct', max_length=10)),
                ('reason', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name='+', to=settings.AUTH_USER_MODEL)),
                ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name='transitions', to='properties.property')),
            ],
            options={'db_table': 'property_transitions', 'ordering': ['-created_at', '-id'],
                     'indexes': [models.Index(fields=['property', '-created_at'], name='prop_transition_idx')]},
        ),
        migrations.RunSQL(CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                f"ALTER TABLE properties ADD CONSTRAINT {CONSTRAINT} CHECK (status IN ({_IN})) NOT VALID",
                DROP_CONSTRAINT_SQL)],
            state_operations=[migrations.AddConstraint('property', models.CheckConstraint(
                condition=models.Q(status__in=list(STATUSES)), name=CONSTRAINT))],
        ),
    ]
