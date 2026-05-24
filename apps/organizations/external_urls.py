from django.urls import path
from .external_api import (
    DeveloperApiKeyListCreateView,
    DeveloperApiKeyRevokeView,
    ExternalLeadsView,
    ExternalLeadDetailView,
    ExternalInventoryView,
)

urlpatterns = [
    # Key management (JWT auth)
    path('keys/',            DeveloperApiKeyListCreateView.as_view(), name='external-api-keys'),
    path('keys/<uuid:key_id>/', DeveloperApiKeyRevokeView.as_view(),  name='external-api-key-revoke'),

    # Tenant-scoped data (API key auth)
    path('leads/',              ExternalLeadsView.as_view(),        name='external-leads'),
    path('leads/<uuid:lead_id>/', ExternalLeadDetailView.as_view(), name='external-lead-detail'),
    path('inventory/',          ExternalInventoryView.as_view(),    name='external-inventory'),
]
