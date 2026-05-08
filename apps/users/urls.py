from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import SendOTPView, VerifyOTPView, MeView, UserListView

urlpatterns = [
    path('otp/send/',      SendOTPView.as_view(),    name='otp-send'),
    path('otp/verify/',    VerifyOTPView.as_view(),  name='otp-verify'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('me/',            MeView.as_view(),         name='me'),
    path('users/',         UserListView.as_view(),   name='user-list'),
    path('users/<uuid:pk>/', UserListView.as_view(), name='user-detail'),
]