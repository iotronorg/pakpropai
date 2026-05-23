from django.urls import path
from .views import (
    SendOTPView, VerifyOTPView, MeView, LogoutView,
    CookieTokenRefreshView, UserListView, NotificationPreferencesView,
    CsrfTokenView, PasswordLoginView, RegistrationOTPVerifyView,
    PasswordResetRequestView, PasswordResetConfirmView, PasswordChangeView,
)

urlpatterns = [
    path('csrf/',          CsrfTokenView.as_view(),          name='csrf-token'),
    path('otp/send/',      SendOTPView.as_view(),            name='otp-send'),
    path('otp/verify/',    VerifyOTPView.as_view(),          name='otp-verify'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='token-refresh'),
    path('me/',            MeView.as_view(),                 name='me'),
    path('me/notification-preferences/', NotificationPreferencesView.as_view(), name='notification-preferences'),
    path('logout/',        LogoutView.as_view(),             name='logout'),
    path('users/',         UserListView.as_view(),           name='user-list'),
    path('users/<uuid:pk>/', UserListView.as_view(),         name='user-detail'),
    # Password auth
    path('login/',                      PasswordLoginView.as_view(),        name='password-login'),
    path('registration/verify-otp/',    RegistrationOTPVerifyView.as_view(), name='registration-verify-otp'),
    path('password/reset/request/',     PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password/reset/confirm/',     PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
    path('password/change/',            PasswordChangeView.as_view(),        name='password-change'),
]