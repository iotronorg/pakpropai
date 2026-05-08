from django.urls import path
from .views import (
    DealLockInitiateView,
    DealLockConfirmView,
    DealLockCancelView,
    DealLockListView,
    MyDealLocksView,
    DealLockDetailView,
)

urlpatterns = [
    path('',                          DealLockListView.as_view(),    name='deal-list'),
    path('mine/',                     MyDealLocksView.as_view(),     name='deal-mine'),
    path('lock/',                     DealLockInitiateView.as_view(), name='deal-initiate'),
    path('lock/<uuid:pk>/',           DealLockDetailView.as_view(),  name='deal-detail'),
    path('lock/<uuid:pk>/confirm/',   DealLockConfirmView.as_view(), name='deal-confirm'),
    path('lock/<uuid:pk>/cancel/',    DealLockCancelView.as_view(),  name='deal-cancel'),
]
