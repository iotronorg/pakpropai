from rest_framework.routers import DefaultRouter
from .views import LeadViewSet

router = DefaultRouter()
router.register('', LeadViewSet, basename='leads')

urlpatterns = router.urls
