from django.urls import path

from .public_views import PublicListingDetailView, PublicListingSearchView

urlpatterns = [
    path('search/', PublicListingSearchView.as_view(), name='public-listing-search'),
    path('<str:slug_or_id>/', PublicListingDetailView.as_view(), name='public-listing-detail'),
]
