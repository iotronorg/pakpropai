"""Django admin login with the second factor (T-1011). Installed in UsersConfig.ready()."""
from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm

from . import mfa


class MfaAdminAuthenticationForm(AdminAuthenticationForm):
    otp_code = forms.CharField(
        label='Authenticator code', required=False, max_length=20,
        widget=forms.TextInput(attrs={'autocomplete': 'one-time-code', 'inputmode': 'numeric'}),
        help_text='6-digit code from your authenticator app, or a recovery code.')

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)          # active + is_staff
        if user.mfa_enabled:
            code = (self.cleaned_data.get('otp_code') or '').strip()
            if not code:
                raise forms.ValidationError('Enter the code from your authenticator app.',
                                            code='mfa_required')
            try:
                mfa.verify_code(user, code)
            except mfa.MFAError as exc:
                raise forms.ValidationError(str(exc), code='mfa_invalid') from None
        elif settings.MFA_ENFORCE:
            raise forms.ValidationError(
                'Multi-factor authentication is required. Set it up in the app '
                '(Account → Security), then log in here.', code='mfa_enrollment_required')
