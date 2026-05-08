from django.urls import path
from .views import ConfigView

urlpatterns = [
    path('', ConfigView.as_view(), name='config'),
]
