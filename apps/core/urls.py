from django.urls import path
from .audit_views import AuditChainVerifyView, AuditEventListView
from .views import HealthCheckView, AuditLogView

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health'),
]

api_urlpatterns = [
    path('audit-log/', AuditLogView.as_view(), name='audit-log'),
    path('audit-events/', AuditEventListView.as_view(), name='audit-events'),
    path('audit-events/verify/', AuditChainVerifyView.as_view(), name='audit-events-verify'),
]
