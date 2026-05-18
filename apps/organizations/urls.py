from django.urls import path
from .views import OrganizationListView, OrganizationDetailView, OrganizationMeView, OrgConfigView

urlpatterns = [
    path('',                       OrganizationListView.as_view(),    name='organizations-list'),
    path('me/',                    OrganizationMeView.as_view(),      name='organizations-me'),
    path('me/config/',             OrgConfigView.as_view(),           name='organizations-config'),
    path('me/config/<str:key>/',   OrgConfigView.as_view(),           name='organizations-config-key'),
    path('<uuid:pk>/',             OrganizationDetailView.as_view(),  name='organizations-detail'),
]
