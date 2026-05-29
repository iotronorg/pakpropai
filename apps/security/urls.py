from django.urls import path
from apps.security.views import SecurityEventListView, ChainVerifyView

urlpatterns = [
    path('events/',       SecurityEventListView.as_view(), name='security-events'),
    path('chain-verify/', ChainVerifyView.as_view(),       name='security-chain-verify'),
]
