from django.urls import path
from .views import AgentMeView, AgentListView, AgentAdminDetailView

urlpatterns = [
    path('',         AgentListView.as_view(),       name='agents-list'),
    path('me/',      AgentMeView.as_view(),          name='agents-me'),
    path('<int:pk>/', AgentAdminDetailView.as_view(), name='agents-admin-detail'),
]
