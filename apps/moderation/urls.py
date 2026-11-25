from django.urls import path

from . import thread_views as T
from . import views as V

urlpatterns = [
    path('queues/', V.QueueSummaryView.as_view(), name='moderation-queues'),
    path('items/', V.ItemListView.as_view(), name='moderation-items'),
    path('items/<uuid:pk>/', V.ItemDetailView.as_view(), name='moderation-item'),
    path('items/<uuid:pk>/decision/', V.DecisionView.as_view(), name='moderation-decision'),
    path('appeals/', V.AppealView.as_view(), name='moderation-appeal'),
    path('threads/', T.ThreadReportListView.as_view(), name='moderation-threads'),
    path('threads/<uuid:pk>/', T.ThreadReportDetailView.as_view(), name='moderation-thread'),
    path('threads/<uuid:pk>/decision/', T.ThreadReportDecisionView.as_view(), name='moderation-thread-decision'),
    path('threads/<uuid:pk>/attachments/<uuid:attachment_id>/', T.ThreadReportAttachmentView.as_view(),
         name='moderation-thread-attachment'),
]
