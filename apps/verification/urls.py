from django.urls import path
from .views import FraudCheckView

urlpatterns = [
    path('fraud-check/', FraudCheckView.as_view(), name='fraud-check'),
]