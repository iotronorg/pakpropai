from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CampaignViewSet, CampaignTemplateListView

router = DefaultRouter()
router.register(r'', CampaignViewSet, basename='campaign')

urlpatterns = [
    path('templates/', CampaignTemplateListView.as_view(), name='campaign-templates'),
    path('', include(router.urls)),
]
