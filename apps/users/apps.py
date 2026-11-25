from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"

    def ready(self):
        # T-1011: the admin login asks for the authenticator code.
        from django.contrib import admin
        from .admin_mfa import MfaAdminAuthenticationForm
        admin.site.login_form = MfaAdminAuthenticationForm
        admin.site.login_template = 'admin/login.html'

        from django.db.models.signals import pre_save
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
        pre_save.connect(_drop_token, sender=OutstandingToken, dispatch_uid='drop_refresh_token')


def _drop_token(sender, instance, **kwargs):
    """Revocation needs the jti only; a stored refresh token could be used by whoever reads the table (KI-102)."""
    instance.token = ''
