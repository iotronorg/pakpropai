from django.urls import path

from .pending_views import PendingActionConfirmView, PendingActionDeclineView, PendingActionListView

urlpatterns = [
    path('', PendingActionListView.as_view(), name='pending-action-list'),
    path('<uuid:pk>/confirm/', PendingActionConfirmView.as_view(), name='pending-action-confirm'),
    path('<uuid:pk>/decline/', PendingActionDeclineView.as_view(), name='pending-action-decline'),
]
