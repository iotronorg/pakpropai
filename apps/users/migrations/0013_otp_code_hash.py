"""
T-1010 / KI-016: replace plaintext ``otp_codes.code`` with a keyed HMAC ``code_hash``.

Forward: add ``code_hash``, hash every existing code (bound to phone + purpose),
drop ``code``. Hashing uses ``apps.core.crypto.blind_index`` (FIELD_HASH_KEY or
the field-encryption key), which must be configured — production already
requires it (ADR-008).

Reverse: re-adds an empty ``code`` column. Hashes cannot be reversed, so every
still-unused code is marked used; affected users simply request a new OTP
(codes live 5 minutes anyway).
"""
from django.db import migrations, models


def hash_existing_codes(apps, schema_editor):
    from apps.core.crypto import blind_index

    OTPCode = apps.get_model('users', 'OTPCode')
    for row in OTPCode.objects.exclude(code='').only('id', 'phone', 'purpose', 'code').iterator():
        digest = blind_index(f'otp:v1:{row.purpose}:{row.phone}:{row.code}')
        OTPCode.objects.filter(pk=row.pk).update(code_hash=digest, code='')


def invalidate_outstanding_codes(apps, schema_editor):
    OTPCode = apps.get_model('users', 'OTPCode')
    OTPCode.objects.filter(is_used=False).update(is_used=True)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0012_encrypt_sensitive_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='otpcode',
            name='code_hash',
            field=models.CharField(default='', max_length=64),
        ),
        migrations.AlterField(
            model_name='otpcode',
            name='code',
            field=models.CharField(default='', max_length=6),
        ),
        migrations.RunPython(hash_existing_codes, invalidate_outstanding_codes),
        migrations.RemoveField(
            model_name='otpcode',
            name='code',
        ),
    ]
