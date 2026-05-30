from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BrokerNetworkPartnershipViewSet,
    CommissionLedgerView,
    SyndicationBrowseView,
    SyndicationLeadSubmissionViewSet,
    SyndicationListingViewSet,
)

router = DefaultRouter()
router.register('listings', SyndicationListingViewSet, basename='syndication-listing')
router.register('partnerships', BrokerNetworkPartnershipViewSet, basename='broker-partnership')
router.register('submissions', SyndicationLeadSubmissionViewSet, basename='syndication-submission')
router.register('ledger', CommissionLedgerView, basename='commission-ledger')

urlpatterns = [
    path('browse/', SyndicationBrowseView.as_view(), name='syndication-browse'),
    path('', include(router.urls)),
]
