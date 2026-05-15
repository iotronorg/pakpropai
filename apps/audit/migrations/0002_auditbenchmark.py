import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditBenchmark',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city', models.CharField(help_text='e.g. lahore, islamabad, karachi, default', max_length=100)),
                ('location_key', models.CharField(help_text='e.g. dha, bahria, gulberg, default', max_length=100)),
                ('ppm_min', models.BigIntegerField(help_text='Min price per marla (PKR)')),
                ('ppm_max', models.BigIntegerField(help_text='Max price per marla (PKR)')),
                ('yield_pct', models.FloatField(help_text='Expected rental yield %')),
                ('appr_pct', models.FloatField(help_text='Expected annual appreciation %')),
                ('liq_months', models.IntegerField(help_text='Avg months to sell')),
                ('approved', models.BooleanField(blank=True, help_text='Is the area LDA/CDA/RDA approved? Leave blank if unknown.', null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='benchmark_updates', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Audit Benchmark',
                'verbose_name_plural': 'Audit Benchmarks',
                'ordering': ['city', 'location_key'],
            },
        ),
        migrations.AddConstraint(
            model_name='auditbenchmark',
            constraint=models.UniqueConstraint(fields=('city', 'location_key'), name='unique_city_location_benchmark'),
        ),
    ]
