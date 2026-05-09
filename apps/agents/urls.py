from django.urls import path
from .views import AgentMeView, AgentListView, AgentAdminDetailView, TeamView, TeamMemberView

urlpatterns = [
    path('',                       AgentListView.as_view(),       name='agents-list'),
    path('me/',                    AgentMeView.as_view(),          name='agents-me'),
    path('team/',                  TeamView.as_view(),             name='agents-team'),
    path('team/<int:agent_id>/',   TeamMemberView.as_view(),       name='agents-team-member'),
    path('<int:pk>/',              AgentAdminDetailView.as_view(), name='agents-admin-detail'),
]
