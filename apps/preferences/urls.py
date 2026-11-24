from django.urls import path

from . import views as V

urlpatterns = [
    path('favourites/', V.FavouriteListView.as_view(), name='favourite-list'),
    path('favourites/<uuid:listing_id>/', V.FavouriteDetailView.as_view(), name='favourite-detail'),
    path('saved-searches/', V.SavedSearchListView.as_view(), name='saved-search-list'),
    path('saved-searches/<uuid:pk>/', V.SavedSearchDetailView.as_view(), name='saved-search-detail'),
    path('preferences/', V.ProfileView.as_view(), name='preferences'),
    path('preferences/consent/', V.MemoryConsentView.as_view(), name='preferences-consent'),
    path('preferences/<uuid:pk>/', V.ProfileItemView.as_view(), name='preference-item'),
]
