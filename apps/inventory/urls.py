from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inventory.views import ExternalPlatformConnectionViewSet, SyncConflictAlertViewSet

router = DefaultRouter()
router.register(r'connections', ExternalPlatformConnectionViewSet, basename='inventory-connection')
router.register(r'conflicts',   SyncConflictAlertViewSet,          basename='inventory-conflict')

urlpatterns = [
    path('', include(router.urls)),
]
