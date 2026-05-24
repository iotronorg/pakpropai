from django.urls import path
from .views import (
    WhatsAppWebhookView,
    NotificationListView,
    NotificationDetailView,
    OrgWhatsAppConfigView,
    OrgWhatsAppVerifyView,
    OrgWhatsAppTestMessageView,
    TakeControlView,
    ReleaseControlView,
)

urlpatterns = [
    path('webhook/', WhatsAppWebhookView.as_view(), name='wa-webhook'),
    path('history/',           NotificationListView.as_view(),   name='wa-history-list'),
    path('history/<uuid:pk>/', NotificationDetailView.as_view(), name='wa-history-detail'),
    # Organization WhatsApp credential management (developer/admin only)
    path('config/',                  OrgWhatsAppConfigView.as_view(),       name='wa-config'),
    path('config/verify/',           OrgWhatsAppVerifyView.as_view(),       name='wa-config-verify'),
    path('config/test-message/',     OrgWhatsAppTestMessageView.as_view(),  name='wa-config-test-message'),
    # Agent handover
    path('sessions/<uuid:session_id>/take-control/',    TakeControlView.as_view(),    name='wa-take-control'),
    path('sessions/<uuid:session_id>/release-control/', ReleaseControlView.as_view(), name='wa-release-control'),
]
