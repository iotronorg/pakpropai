from django.urls import path
from .views import (
    LeadReportView, AgentReportView, PropertyReportView, DealReportView,
    RevenueReportView, BotReportView, AgentPersonalReportView,
    ReportGenerateView, ReportStatusView, ReportDownloadView, MyReportsView,
    MonthlyReportListView,
)

urlpatterns = [
    # Analytics dashboards
    path('leads/',      LeadReportView.as_view(),          name='report-leads'),
    path('agents/',     AgentReportView.as_view(),         name='report-agents'),
    path('properties/', PropertyReportView.as_view(),      name='report-properties'),
    path('deals/',      DealReportView.as_view(),          name='report-deals'),
    path('revenue/',    RevenueReportView.as_view(),       name='report-revenue'),
    path('bot/',        BotReportView.as_view(),           name='report-bot'),
    path('my-stats/',   AgentPersonalReportView.as_view(), name='report-my-stats'),

    # Monthly org reports
    path('monthly/',    MonthlyReportListView.as_view(),   name='report-monthly'),

    # User-facing report generation
    path('generate/',                 ReportGenerateView.as_view(),  name='report-generate'),
    path('mine/',                     MyReportsView.as_view(),       name='report-mine'),
    path('<uuid:report_id>/',         ReportStatusView.as_view(),    name='report-status'),
    path('<uuid:report_id>/download/', ReportDownloadView.as_view(), name='report-download'),
]
