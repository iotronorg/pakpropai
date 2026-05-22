from django.urls import path
from . import views

urlpatterns = [
    path('consent/', views.ConsentView.as_view(),      name='compliance-consent'),
    path('export/',  views.DataExportView.as_view(),   name='compliance-export'),
    path('my-data/', views.DataDeletionView.as_view(), name='compliance-deletion'),
]
