"""PriceHistory table (T-1070c, ADR-023): new table, no change to existing ones."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0028_backfill_size_v2'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PriceHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('price', models.BigIntegerField(blank=True, null=True)),
                ('currency', models.CharField(max_length=3)),
                ('price_type', models.CharField(choices=[('total', 'Total'), ('per_month', 'Per Month'), ('per_year', 'Per Year'), ('per_sqm', 'Per m²')], max_length=20)),
                ('price_on_request', models.BooleanField(default=False)),
                ('changed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('source', models.CharField(choices=[('api', 'API'), ('admin', 'Admin'), ('ai_tool', 'AI assistant'), ('system', 'System'), ('backfill', 'Backfill')], default='system', max_length=10)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='price_history', to='properties.property')),
            ],
            options={
                'db_table': 'property_price_history',
                'ordering': ['-changed_at', '-id'],
                'indexes': [models.Index(fields=['property', '-changed_at'], name='price_hist_prop_idx')],
            },
        ),
    ]
