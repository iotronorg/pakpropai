from django.urls import path
from .views import (
    SendOTPView, VerifyOTPView, MeView, LogoutView,
    CookieTokenRefreshView, UserCollectionView, UserDetailView, NotificationPreferencesView,
    CsrfTokenView, PasswordLoginView, RegistrationOTPVerifyView,
    PasswordResetRequestView, PasswordResetConfirmView, PasswordChangeView,
)

from .mfa_views import (MfaStatusView, MfaVerifyView, RecoveryCodesView, TotpConfirmView,
                        TotpDisableView, TotpSetupView)

urlpatterns = [
    # MFA (T-1011)
    path('mfa/',                        MfaStatusView.as_view(),   name='mfa-status'),
    path('mfa/verify/',                 MfaVerifyView.as_view(),   name='mfa-verify'),
    path('mfa/totp/setup/',             TotpSetupView.as_view(),   name='mfa-totp-setup'),
    path('mfa/totp/confirm/',           TotpConfirmView.as_view(), name='mfa-totp-confirm'),
    path('mfa/totp/disable/',           TotpDisableView.as_view(), name='mfa-totp-disable'),
    path('mfa/recovery-codes/',         RecoveryCodesView.as_view(), name='mfa-recovery-codes'),
    path('csrf/',          CsrfTokenView.as_view(),          name='csrf-token'),
    path('otp/send/',      SendOTPView.as_view(),            name='otp-send'),
    path('otp/verify/',    VerifyOTPView.as_view(),          name='otp-verify'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='token-refresh'),
    path('me/',            MeView.as_view(),                 name='me'),
    path('me/notification-preferences/', NotificationPreferencesView.as_view(), name='notification-preferences'),
    path('logout/',        LogoutView.as_view(),             name='logout'),
    path('users/',         UserCollectionView.as_view(),     name='user-list'),
    path('users/<uuid:pk>/', UserDetailView.as_view(),       name='user-detail'),
    # Password auth
    path('login/',                      PasswordLoginView.as_view(),        name='password-login'),
    path('registration/verify-otp/',    RegistrationOTPVerifyView.as_view(), name='registration-verify-otp'),
    path('password/reset/request/',     PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password/reset/confirm/',     PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
    path('password/change/',            PasswordChangeView.as_view(),        name='password-change'),
]