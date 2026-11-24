from django.urls import path

from . import views as V

urlpatterns = [
    path('', V.ThreadListView.as_view(), name='thread-list'),
    path('<uuid:pk>/', V.ThreadDetailView.as_view(), name='thread-detail'),
    path('<uuid:pk>/messages/', V.ThreadMessagesView.as_view(), name='thread-messages'),
    path('<uuid:pk>/read/', V.ThreadReadView.as_view(), name='thread-read'),
    path('<uuid:pk>/sharing/', V.ThreadSharingView.as_view(), name='thread-sharing'),
    path('<uuid:pk>/close/', V.ThreadCloseView.as_view(), name='thread-close'),
]
