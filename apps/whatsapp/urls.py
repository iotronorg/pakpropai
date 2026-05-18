from django.urls import path
from .views import WhatsAppWebhookView, NotificationListView, NotificationDetailView

urlpatterns = [
    path('webhook/', WhatsAppWebhookView.as_view(), name='wa-webhook'),
    # Session + message history for dashboard (agent / developer / admin)
    # GET /api/v1/whatsapp/history/            — paginated session list
    # GET /api/v1/whatsapp/history/<uuid>/     — full message thread
    path('history/',           NotificationListView.as_view(),   name='wa-history-list'),
    path('history/<uuid:pk>/', NotificationDetailView.as_view(), name='wa-history-detail'),
]