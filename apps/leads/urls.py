from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter
from .views import LeadViewSet, AppointmentViewSet
from .admin_views import DuplicateLeadView, BulkAssignLeadsView, MergeLeadsView

router = DefaultRouter()
router.register('', LeadViewSet, basename='leads')

# SimpleRouter (not DefaultRouter) avoids generating an api-root at ^$ that
# would intercept the leads-list URL when appt_router.urls is placed first.
appt_router = SimpleRouter()
appt_router.register('appointments', AppointmentViewSet, basename='appointments')

urlpatterns = [
    # Fixed paths and appt_router MUST come before router.urls so they are not
    # swallowed by the leads router's ^(?P<pk>[^/.]+)/$ detail pattern.
    path('duplicates/',  DuplicateLeadView.as_view(),    name='lead-duplicates'),
    path('bulk-assign/', BulkAssignLeadsView.as_view(),  name='leads-bulk-assign'),
    path('merge/',       MergeLeadsView.as_view(),       name='leads-merge'),
] + appt_router.urls + router.urls
