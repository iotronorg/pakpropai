from django.urls import path
from .views import AgentMeView, AgentListView

urlpatterns = [
    path('',    AgentListView.as_view(), name='agents-list'),
    path('me/', AgentMeView.as_view(),   name='agents-me'),
]
