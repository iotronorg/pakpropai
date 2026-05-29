from django.urls import path
from .views import SlaStatusView, SlaCircuitResetView

urlpatterns = [
    path('status/',                         SlaStatusView.as_view(),        name='sla-status'),
    path('circuits/<str:service>/reset/',   SlaCircuitResetView.as_view(),  name='sla-circuit-reset'),
]
