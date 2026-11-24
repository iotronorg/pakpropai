from django.urls import path

from .views import ConversationDetailView, ConversationListView, ConversationStateView, MessageListView

urlpatterns = [
    path('', ConversationListView.as_view(), name='conversation-list'),
    path('<uuid:pk>/', ConversationDetailView.as_view(), name='conversation-detail'),
    path('<uuid:pk>/messages/', MessageListView.as_view(), name='conversation-messages'),
    path('<uuid:pk>/state/', ConversationStateView.as_view(), name='conversation-state'),
]
