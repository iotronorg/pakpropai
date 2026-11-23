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

