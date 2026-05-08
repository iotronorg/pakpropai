from django.urls import path
from .views import (
    FraudCheckView,
    VerificationQueueView,
    VerificationReviewView,
    DocumentScanListView,
    LinkDocumentToVerificationView,
)

urlpatterns = [
    path('fraud-check/',                                      FraudCheckView.as_view(),               name='fraud-check'),
    path('queue/',                                            VerificationQueueView.as_view(),         name='verification-queue'),
    path('queue/<uuid:pk>/',                                  VerificationReviewView.as_view(),        name='verification-review'),
    path('documents/',                                        DocumentScanListView.as_view(),          name='document-scan-list'),
    path('documents/<int:scan_id>/link/<uuid:verification_id>/', LinkDocumentToVerificationView.as_view(), name='link-document'),
]
