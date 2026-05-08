from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import LeadViewSet, AppointmentViewSet, DuplicateLeadView

router = DefaultRouter()
router.register('', LeadViewSet, basename='leads')

appt_router = DefaultRouter()
appt_router.register('appointments', AppointmentViewSet, basename='appointments')

urlpatterns = router.urls + appt_router.urls + [
    path('duplicates/', DuplicateLeadView.as_view(), name='lead-duplicates'),
]
