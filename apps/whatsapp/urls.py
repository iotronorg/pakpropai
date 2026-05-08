from django.urls import path
from .views import WhatsAppWebhookView, NotificationListView, NotificationDetailView

urlpatterns = [
    path('webhook/', WhatsAppWebhookView.as_view(), name='wa-webhook'),
]

notification_urlpatterns = [
    path('',        NotificationListView.as_view(),           name='notification-list'),
    path('<uuid:pk>/', NotificationDetailView.as_view(),      name='notification-detail'),
]