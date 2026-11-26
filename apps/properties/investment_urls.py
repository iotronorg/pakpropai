from django.urls import path

from .investment_views import CalculateView, CommentaryView

urlpatterns = [
    path('calculate/', CalculateView.as_view(), name='investment-calculate'),
    path('commentary/', CommentaryView.as_view(), name='investment-commentary'),
]
