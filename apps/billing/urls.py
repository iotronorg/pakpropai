from django.urls import path
from .views import BillingUsageView

urlpatterns = [
    path('usage/', BillingUsageView.as_view(), name='billing-usage'),
]
