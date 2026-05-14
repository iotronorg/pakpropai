from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import PropertyViewSet, PropertyCompareView, PropertyMarketTrendsView

router = DefaultRouter()
router.register(r'', PropertyViewSet, basename='property')

urlpatterns = router.urls + [
    path('compare/',       PropertyCompareView.as_view(),       name='property-compare'),
    path('market-trends/', PropertyMarketTrendsView.as_view(),  name='property-market-trends'),
]