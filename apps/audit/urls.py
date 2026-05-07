from django.urls import path
from . import views

urlpatterns = [
    path('download/<int:audit_id>/', views.download_pdf, name='audit-download'),
]
