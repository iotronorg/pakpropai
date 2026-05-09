from django.urls import path
from . import views

urlpatterns = [
    path('',                          views.AuditListView.as_view(), name='audit-list'),
    path('download/<int:audit_id>/',  views.download_pdf,            name='audit-download'),
]
