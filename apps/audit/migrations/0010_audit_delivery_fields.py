import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('audit', '0009_uae_benchmarks'),
    ]
    operations = [
        migrations.AddField(
            model_name='propertyaudit',
            name='delivery_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'), ('generating', 'Generating'),
                    ('ready', 'Ready'), ('sent', 'Sent'), ('failed', 'Failed'),
                ],
                default='pending',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='propertyaudit',
            name='cloudinary_url',
            field=models.URLField(blank=True),
        ),
        migrations.CreateModel(
            name='AuditDeliveryFailure',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('failure_type', models.CharField(max_length=20)),
                ('error_detail', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('audit', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='delivery_failures',
                    to='audit.propertyaudit',
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
