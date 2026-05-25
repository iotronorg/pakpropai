from django.urls import path
from .views import (
    FraudCheckView,
    VerificationQueueView,
    VerificationReviewView,
    BulkRejectVerificationsView,
    DocumentScanListView,
    DocumentScanDetailView,
    LinkDocumentToVerificationView,
    FraudStatsView,
    FraudAlertsView,
    FraudBlacklistView,
    FraudBlacklistDeleteView,
    FlaggedUsersView,
    TrustCertificateView,
)

urlpatterns = [
    path('fraud-check/',                                          FraudCheckView.as_view(),              name='fraud-check'),
    path('queue/',                                                VerificationQueueView.as_view(),        name='verification-queue'),
    path('queue/<uuid:pk>/',                                      VerificationReviewView.as_view(),       name='verification-review'),
    path('<uuid:property_id>/certificate/',                        TrustCertificateView.as_view(),        name='trust-certificate'),
    path('bulk-reject/',                                          BulkRejectVerificationsView.as_view(), name='verification-bulk-reject'),
    path('documents/',                                            DocumentScanListView.as_view(),           name='document-scan-list'),
    path('documents/<int:pk>/',                                   DocumentScanDetailView.as_view(),          name='document-scan-detail'),
    path('documents/<int:scan_id>/link/<uuid:verification_id>/',  LinkDocumentToVerificationView.as_view(), name='link-document'),
    # Fraud monitoring
    path('fraud/stats/',                   FraudStatsView.as_view(),           name='fraud-stats'),
    path('fraud/alerts/',                  FraudAlertsView.as_view(),          name='fraud-alerts'),
    path('fraud/blacklist/',               FraudBlacklistView.as_view(),       name='fraud-blacklist'),
    path('fraud/blacklist/<int:pk>/',      FraudBlacklistDeleteView.as_view(), name='fraud-blacklist-delete'),
    path('fraud/users/',                   FlaggedUsersView.as_view(),         name='fraud-users'),
]
