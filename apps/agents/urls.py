from django.urls import path
from .views import (
    AgentMeView,
    AgentListView,
    AgentAdminDetailView,
    AgentRegisterView,
    AgentApproveView,
    AgentRejectView,
    TeamView,
    TeamMemberView,
)

urlpatterns = [
    path('',                          AgentListView.as_view(),        name='agents-list'),
    path('me/',                       AgentMeView.as_view(),           name='agents-me'),
    path('register/',                 AgentRegisterView.as_view(),     name='agents-register'),
    path('team/',                     TeamView.as_view(),              name='agents-team'),
    path('team/<int:agent_id>/',      TeamMemberView.as_view(),        name='agents-team-member'),
    path('<int:pk>/',                 AgentAdminDetailView.as_view(),  name='agents-admin-detail'),
    path('<int:pk>/approve/',         AgentApproveView.as_view(),      name='agents-approve'),
    path('<int:pk>/reject/',          AgentRejectView.as_view(),       name='agents-reject'),
]
