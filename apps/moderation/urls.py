from django.urls import path

from . import views as V

urlpatterns = [
    path('queues/', V.QueueSummaryView.as_view(), name='moderation-queues'),
    path('items/', V.ItemListView.as_view(), name='moderation-items'),
    path('items/<uuid:pk>/', V.ItemDetailView.as_view(), name='moderation-item'),
    path('items/<uuid:pk>/decision/', V.DecisionView.as_view(), name='moderation-decision'),
    path('appeals/', V.AppealView.as_view(), name='moderation-appeal'),
]
