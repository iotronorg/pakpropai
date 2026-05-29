from django.urls import path
from .public_views import (
    PublicVerifyRedirectView,
    PublicReferralConvertView,
    PublicPlatformStatsView,
)

urlpatterns = [
    path('verify/',           PublicVerifyRedirectView.as_view(),  name='public-verify'),
    path('referral/convert/', PublicReferralConvertView.as_view(), name='public-referral-convert'),
    path('platform-stats/',   PublicPlatformStatsView.as_view(),   name='public-platform-stats'),
]
