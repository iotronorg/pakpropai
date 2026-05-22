from django.db import migrations, models


def mark_existing_users_verified(apps, schema_editor):
    """Existing users all verified their phone via OTP — mark them verified."""
    User = apps.get_model('users', 'User')
    User.objects.update(is_phone_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_data_migrate_admin_platform_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='is_phone_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='otpcode',
            name='purpose',
            field=models.CharField(
                choices=[
                    ('otp_login', 'OTP Login'),
                    ('registration_verify', 'Registration Verify'),
                    ('password_reset', 'Password Reset'),
                ],
                default='otp_login',
                max_length=30,
            ),
        ),
        migrations.RunPython(mark_existing_users_verified, migrations.RunPython.noop),
    ]
