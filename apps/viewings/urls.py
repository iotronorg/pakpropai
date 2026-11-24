from django.urls import path

from . import views as V

urlpatterns = [
    path('', V.ViewingListView.as_view(), name='viewing-list'),
    path('<uuid:pk>/', V.ViewingDetailView.as_view(), name='viewing-detail'),
    path('<uuid:pk>/cancel/', V.ViewingCancelView.as_view(), name='viewing-cancel'),
    path('<uuid:pk>/reschedule/', V.ViewingRescheduleView.as_view(), name='viewing-reschedule'),
    path('<uuid:pk>/feedback/', V.ViewingFeedbackView.as_view(), name='viewing-feedback'),
]
