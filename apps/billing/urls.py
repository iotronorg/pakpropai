from django.urls import path
from .views import (
    BillingUsageView,
    BillingCheckoutView,
    BillingPortalView,
    BillingInvoiceView,
    OrgPaymentSettingsView,
    StripeWebhookView,
    SafepayBillingWebhookView,
    BSecureBillingWebhookView,
)

urlpatterns = [
    path('usage/',             BillingUsageView.as_view(),           name='billing-usage'),
    path('checkout/',          BillingCheckoutView.as_view(),         name='billing-checkout'),
    path('portal/',            BillingPortalView.as_view(),           name='billing-portal'),
    path('invoices/',          BillingInvoiceView.as_view(),          name='billing-invoices'),
    path('payment-settings/',  OrgPaymentSettingsView.as_view(),      name='billing-payment-settings'),
    path('webhook/stripe/',    StripeWebhookView.as_view(),           name='stripe-webhook'),
    path('webhook/safepay/',   SafepayBillingWebhookView.as_view(),   name='safepay-billing-webhook'),
    path('webhook/bsecure/',   BSecureBillingWebhookView.as_view(),   name='bsecure-billing-webhook'),
]
