from django.urls import path
from .views import (
    CreateCheckoutView,
    PaymentReturnView,
    SafepayWebhookView,
    bSecureWebhookView,
    SafepayDealLockWebhookView,
    bSecureDealLockWebhookView,
    StripeDealLockWebhookView,
    SEPAPaymentView,
    PaymentListView,
)

urlpatterns = [
    path('',                                    PaymentListView.as_view(),              name='payment-list'),
    path('checkout/<uuid:deal_id>/',            CreateCheckoutView.as_view(),           name='payment-checkout'),
    path('sepa/<uuid:deal_id>/',               SEPAPaymentView.as_view(),              name='payment-sepa'),
    path('return/',                             PaymentReturnView.as_view(),            name='payment-return'),
    path('webhook/safepay/',                    SafepayWebhookView.as_view(),           name='webhook-safepay'),
    path('webhook/bsecure/',                    bSecureWebhookView.as_view(),           name='webhook-bsecure'),
    path('webhook/stripe/deal-lock/',           StripeDealLockWebhookView.as_view(),    name='webhook-stripe-deal-lock'),
    path('webhook/safepay/deal-lock/',          SafepayDealLockWebhookView.as_view(),   name='webhook-safepay-deal-lock'),
    path('webhook/bsecure/deal-lock/',          bSecureDealLockWebhookView.as_view(),   name='webhook-bsecure-deal-lock'),
]
