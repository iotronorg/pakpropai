from django.urls import path
from .views import (
    LeadReportView, AgentReportView, PropertyReportView,
    RevenueReportView, BotReportView, AgentPersonalReportView,
    ReportGenerateView, ReportStatusView, ReportDownloadView, MyReportsView,
)

urlpatterns = [
    # Analytics dashboards
    path('leads/',      LeadReportView.as_view(),          name='report-leads'),
    path('agents/',     AgentReportView.as_view(),         name='report-agents'),
    path('properties/', PropertyReportView.as_view(),      name='report-properties'),
    path('revenue/',    RevenueReportView.as_view(),       name='report-revenue'),
    path('bot/',        BotReportView.as_view(),           name='report-bot'),
    path('my-stats/',   AgentPersonalReportView.as_view(), name='report-my-stats'),

    # User-facing report generation
    path('generate/',                 ReportGenerateView.as_view(),  name='report-generate'),
    path('mine/',                     MyReportsView.as_view(),       name='report-mine'),
    path('<uuid:report_id>/',         ReportStatusView.as_view(),    name='report-status'),
    path('<uuid:report_id>/download/', ReportDownloadView.as_view(), name='report-download'),
]
