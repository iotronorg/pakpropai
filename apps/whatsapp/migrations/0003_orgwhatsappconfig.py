import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0002_whatsappmessage_media_id'),
        ('organizations', '0016_data_migrate_membership_admin_to_org_admin'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrgWhatsAppConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('organization', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='whatsapp_config',
                    to='organizations.organization',
                )),
                ('phone_number_id', models.CharField(blank=True, db_index=True, max_length=50,
                    help_text="Meta phone_number_id for this org's WA number")),
                ('display_phone', models.CharField(blank=True, max_length=20,
                    help_text='Human-readable E.164 phone number, e.g. +923001234567')),
                ('access_token', models.CharField(blank=True, max_length=500,
                    help_text='Meta System User token — never returned by API')),
                ('app_id', models.CharField(blank=True, max_length=50,
                    help_text='Meta App ID')),
                ('app_secret', models.CharField(blank=True, max_length=200,
                    help_text='Meta App Secret for HMAC-SHA256 webhook verification — never returned by API')),
                ('verify_token', models.CharField(blank=True, max_length=200,
                    help_text='Custom webhook verify token — never returned by API')),
                ('is_active', models.BooleanField(default=False,
                    help_text='Enable/disable this integration')),
                ('webhook_verified_at', models.DateTimeField(blank=True, null=True,
                    help_text='Set when Meta successfully verifies the webhook')),
                ('ai_enabled', models.BooleanField(default=True,
                    help_text="Toggle AI automation for this org's WhatsApp")),
                ('auto_reply_enabled', models.BooleanField(default=True,
                    help_text='Toggle auto-replies')),
                ('otp_template_name', models.CharField(blank=True, max_length=100,
                    help_text='Per-org OTP template name (overrides platform default)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'org_whatsapp_configs'},
        ),
    ]
