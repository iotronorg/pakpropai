from django.urls import path
from .views import HealthCheckView, AuditLogView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health'),
]

api_urlpatterns = [
    path('audit-log/', AuditLogView.as_view(), name='audit-log'),
]
