from django.urls import path
from . import views

urlpatterns = [
    path('consent/',              views.ConsentView.as_view(),             name='compliance-consent'),
    path('export/',               views.DataExportView.as_view(),          name='compliance-export'),
    path('my-data/',              views.DataDeletionView.as_view(),        name='compliance-deletion'),
    path('privacy-audit/',        views.PrivacyAuditLogView.as_view(),     name='privacy-audit-log'),
    path('privacy-audit/export/', views.PrivacyAuditExportView.as_view(),  name='privacy-audit-export'),
    path('rtbf/initiate/',        views.RightToBeForgottenView.as_view(),  name='rtbf-initiate'),
    path('rtbf/<uuid:request_id>/', views.RTBFStatusView.as_view(),        name='rtbf-status'),
    path('pii-detections/summary/', views.PIIDetectionSummaryView.as_view(), name='pii-detection-summary'),
    # AML screening
    path('screenings/',                  views.ComplianceScreeningListView.as_view(),   name='compliance-screenings'),
    path('screenings/<uuid:screening_id>/', views.ComplianceScreeningDetailView.as_view(), name='compliance-screening-detail'),
    path('sanctions/',                   views.ComplianceSanctionListView.as_view(),    name='compliance-sanctions'),
    path('sanctions/<uuid:record_id>/',  views.ComplianceSanctionDetailView.as_view(),  name='compliance-sanction-detail'),
    path('screenings/export/',            views.ComplianceReportExportView.as_view(),    name='compliance-report-export'),
]
