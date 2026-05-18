from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import LeadViewSet, AppointmentViewSet
from .admin_views import DuplicateLeadView, BulkAssignLeadsView, MergeLeadsView

router = DefaultRouter()
router.register('', LeadViewSet, basename='leads')

appt_router = DefaultRouter()
appt_router.register('appointments', AppointmentViewSet, basename='appointments')

urlpatterns = [
    # Fixed paths MUST come before router.urls so they are not swallowed by
    # the router's ^(?P<pk>[^/.]+)/$ detail pattern.
    path('duplicates/',  DuplicateLeadView.as_view(),    name='lead-duplicates'),
    path('bulk-assign/', BulkAssignLeadsView.as_view(),  name='leads-bulk-assign'),
    path('merge/',       MergeLeadsView.as_view(),       name='leads-merge'),
] + router.urls + appt_router.urls
