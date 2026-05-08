from django.urls import path
from .views import (
    CreateCheckoutView,
    PaymentReturnView,
    SafepayWebhookView,
    bSecureWebhookView,
    PaymentListView,
)

urlpatterns = [
    path('',                             PaymentListView.as_view(),     name='payment-list'),
    path('checkout/<uuid:deal_id>/',     CreateCheckoutView.as_view(),  name='payment-checkout'),
    path('return/',                      PaymentReturnView.as_view(),   name='payment-return'),
    path('webhook/safepay/',             SafepayWebhookView.as_view(),  name='webhook-safepay'),
    path('webhook/bsecure/',             bSecureWebhookView.as_view(),  name='webhook-bsecure'),
]
