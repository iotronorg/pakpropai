from django.urls import path

from .comparison_views import PublicCompareView
from .public_views import PublicListingDetailView, PublicListingSearchView

urlpatterns = [
    path('search/', PublicListingSearchView.as_view(), name='public-listing-search'),
    path('compare/', PublicCompareView.as_view(), name='public-listing-compare'),
    path('<str:slug_or_id>/', PublicListingDetailView.as_view(), name='public-listing-detail'),
]
