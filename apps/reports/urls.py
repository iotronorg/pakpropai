from django.urls import path
from .views import LeadReportView, AgentReportView, PropertyReportView

urlpatterns = [
    path('leads/',      LeadReportView.as_view(),    name='report-leads'),
    path('agents/',     AgentReportView.as_view(),   name='report-agents'),
    path('properties/', PropertyReportView.as_view(), name='report-properties'),
]
