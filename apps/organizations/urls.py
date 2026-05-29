from django.urls import path
from .views import (
    OrganizationListView, OrganizationDetailView, OrganizationMeView,
    OrgConfigView, OrgDashboardView, OrgAIStatsView,
    OrganizationSuspendView, OrganizationActivateView,
    AdminOrgConfigView, OrgRegistrationView, OrgRegistrationOTPVerifyView,
)
from .theme_views import OrgThemeView

urlpatterns = [
    path('register/',                      OrgRegistrationView.as_view(),          name='org-register'),
    path('register/verify-otp/',           OrgRegistrationOTPVerifyView.as_view(), name='org-register-verify-otp'),
    path('',                               OrganizationListView.as_view(),         name='organizations-list'),
    path('me/',                           OrganizationMeView.as_view(),       name='organizations-me'),
    path('me/config/',                    OrgConfigView.as_view(),            name='organizations-config'),
    path('me/config/<str:key>/',          OrgConfigView.as_view(),            name='organizations-config-key'),
    path('me/dashboard/',                 OrgDashboardView.as_view(),         name='organizations-dashboard'),
    path('me/ai-stats/',                  OrgAIStatsView.as_view(),           name='organizations-ai-stats'),
    path('me/theme/',                     OrgThemeView.as_view(),             name='org-theme'),
    path('<uuid:pk>/suspend/',            OrganizationSuspendView.as_view(),  name='org-suspend'),
    path('<uuid:pk>/activate/',           OrganizationActivateView.as_view(), name='org-activate'),
    path('<uuid:pk>/config/',             AdminOrgConfigView.as_view(),       name='admin-org-config'),
    path('<uuid:pk>/config/<str:key>/',   AdminOrgConfigView.as_view(),       name='admin-org-config-key'),
    path('<uuid:pk>/',                    OrganizationDetailView.as_view(),   name='organizations-detail'),
]
