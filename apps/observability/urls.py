from django.urls import path
from .views import ObservabilityMetricsView, TraceSearchView, OtelHealthView

urlpatterns = [
    path('metrics/', ObservabilityMetricsView.as_view(), name='observability-metrics'),
    path('traces/',  TraceSearchView.as_view(),          name='observability-traces'),
    path('health/',  OtelHealthView.as_view(),            name='observability-health'),
]
