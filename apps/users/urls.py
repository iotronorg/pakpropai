from django.urls import path
from .views import SendOTPView, VerifyOTPView, MeView, LogoutView, CookieTokenRefreshView, UserListView

urlpatterns = [
    path('otp/send/',      SendOTPView.as_view(),            name='otp-send'),
    path('otp/verify/',    VerifyOTPView.as_view(),          name='otp-verify'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='token-refresh'),
    path('me/',            MeView.as_view(),                 name='me'),
    path('logout/',        LogoutView.as_view(),             name='logout'),
    path('users/',         UserListView.as_view(),           name='user-list'),
    path('users/<uuid:pk>/', UserListView.as_view(),         name='user-detail'),
]