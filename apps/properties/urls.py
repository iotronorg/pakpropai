from django.urls import path
from rest_framework.routers import DefaultRouter
from .assistant_views import DescribeView, ExtractView, MissingInfoView
from .views import (PropertyCompareView, PropertyMarketTrendsView, PropertyTaxonomyView,
                    PropertyViewSet)

router = DefaultRouter()
router.register(r'', PropertyViewSet, basename='property')

# Explicit paths must precede the router: its detail route (^<pk>/$) would
# otherwise capture 'compare/' and 'market-trends/'.
urlpatterns = [
    path('compare/',       PropertyCompareView.as_view(),       name='property-compare'),
    path('market-trends/', PropertyMarketTrendsView.as_view(),  name='property-market-trends'),
    path('taxonomy/',      PropertyTaxonomyView.as_view(),      name='property-taxonomy'),
    # T-3050 listing assistant
    path('<uuid:pk>/ai/missing-info/', MissingInfoView.as_view(), name='property-ai-missing-info'),
    path('<uuid:pk>/ai/describe/',     DescribeView.as_view(),    name='property-ai-describe'),
    path('<uuid:pk>/ai/extract/',      ExtractView.as_view(),     name='property-ai-extract'),
] + router.urls