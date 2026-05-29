from django.urls import path
from apps.whatsapp.provisioning_views import (
    ProvisioningStatusView,
    ProvisioningStartView,
    ProvisioningRetryView,
)

urlpatterns = [
    path('status/', ProvisioningStatusView.as_view(), name='provisioning-status'),
    path('start/', ProvisioningStartView.as_view(), name='provisioning-start'),
    path('retry/', ProvisioningRetryView.as_view(), name='provisioning-retry'),
]
