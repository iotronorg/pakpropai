"""KI-102: identity data and blacklist entries are encrypted; the blacklist is looked up by a keyed hash."""
from django.db import migrations, models

import apps.core.crypto
from apps.core.crypto import decrypt_columns, decrypt_str, encrypt_columns


def index_tokens(apps, schema_editor):
    from apps.verification.models import FraudBlacklist as Current
    Model = apps.get_model('verification', 'FraudBlacklist')
    seen = set()
    for row in Model.objects.order_by('-created_at'):
        row.token_index = Current.index(decrypt_str(row.token))
        if row.token_index in seen:                  # same entry in another letter case
            row.delete()
            continue
        seen.add(row.token_index)
        row.save(update_fields=['token_index'])


def drop_old_cache_keys(apps, schema_editor):
    """Rebuild the cache under the hashed keys; the old keys hold the plain entry."""
    from apps.verification.models import FraudBlacklist as Current
    from django.core.cache import cache
    try:
        for row in apps.get_model('verification', 'FraudBlacklist').objects.all():
            token = decrypt_str(row.token)
            cache.delete(f'fraud:blacklist:{token.lower()}')
            Current(token=token, expires_at=row.expires_at).sync_to_cache()
    except Exception as exc:                          # no cache during the migration: re-save the entries later
        print(f' blacklist cache not rebuilt: {exc}')


SCAN, VERIF, BLACK = 'verification_documentscan', 'verifications', 'fraud_blacklist'


class Migration(migrations.Migration):

    dependencies = [('verification', '0008_encrypt_sensitive_fields')]

    operations = [
        migrations.AlterField('documentscan', 'extracted_fields', apps.core.crypto.EncryptedJSONField(default=dict)),
        migrations.AlterField('documentscan', 'whatsapp_summary', apps.core.crypto.EncryptedTextField(blank=True)),
        migrations.AlterField('verification', 'ocr_data', apps.core.crypto.EncryptedJSONField(blank=True, default=dict)),
        migrations.AlterField('fraudblacklist', 'token', apps.core.crypto.EncryptedTextField(
            help_text='Keyword, phone, CNIC, society name, or any fraud signal')),
        migrations.AddField('fraudblacklist', 'token_index', models.CharField(max_length=64, null=True, editable=False)),
        migrations.RunPython(encrypt_columns(SCAN, ['extracted_fields', 'whatsapp_summary']),
                             decrypt_columns(SCAN, ['extracted_fields', 'whatsapp_summary'])),
        migrations.RunPython(encrypt_columns(VERIF, ['ocr_data']), decrypt_columns(VERIF, ['ocr_data'])),
        migrations.RunPython(index_tokens, migrations.RunPython.noop),
        migrations.RunPython(drop_old_cache_keys, migrations.RunPython.noop),
        migrations.RunPython(encrypt_columns(BLACK, ['token']), decrypt_columns(BLACK, ['token'])),
        migrations.AlterField('fraudblacklist', 'token_index',
                              models.CharField(max_length=64, unique=True, editable=False)),
    ]
