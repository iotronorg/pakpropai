from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import PropertyViewSet, PropertyCompareView, PropertyMarketTrendsView

router = DefaultRouter()
router.register(r'', PropertyViewSet, basename='property')

# Explicit paths must precede the router: its detail route (^<pk>/$) would
# otherwise capture 'compare/' and 'market-trends/'.
urlpatterns = [
    path('compare/',       PropertyCompareView.as_view(),       name='property-compare'),
    path('market-trends/', PropertyMarketTrendsView.as_view(),  name='property-market-trends'),
] + router.urls