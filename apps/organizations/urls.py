from django.urls import path
from .views import (
    OrganizationListView, OrganizationDetailView, OrganizationMeView,
    OrgConfigView, OrgDashboardView, OrgAIStatsView,
    OrganizationSuspendView, OrganizationActivateView,
    AdminOrgConfigView, AdminOrgConfigKeyView, OrgConfigKeyView, OrgRegistrationView, OrgRegistrationOTPVerifyView,
    OrgMembershipView,
    OrgMembershipDetailView,
    OrgOwnershipTransferView,
    OrgInviteListView, OrgInviteRevokeView, MyInvitesView, InviteRespondView,
)
from .theme_views import OrgThemeView

urlpatterns = [
    path('register/',                      OrgRegistrationView.as_view(),          name='org-register'),
    path('register/verify-otp/',           OrgRegistrationOTPVerifyView.as_view(), name='org-register-verify-otp'),
    path('',                               OrganizationListView.as_view(),         name='organizations-list'),
    path('me/',                           OrganizationMeView.as_view(),       name='organizations-me'),
    path('me/config/',                    OrgConfigView.as_view(),            name='organizations-config'),
    path('me/config/<str:key>/',          OrgConfigKeyView.as_view(),            name='organizations-config-key'),
    path('me/dashboard/',                 OrgDashboardView.as_view(),         name='organizations-dashboard'),
    path('me/ai-stats/',                  OrgAIStatsView.as_view(),           name='organizations-ai-stats'),
    path('me/theme/',                     OrgThemeView.as_view(),             name='org-theme'),
    path('me/members/',                   OrgMembershipView.as_view(),        name='org-members'),
    path('me/members/<int:membership_id>/', OrgMembershipDetailView.as_view(),      name='org-member-detail'),
    path('me/invites/',                   OrgInviteListView.as_view(),        name='org-invites'),
    path('me/invites/<int:invite_id>/',   OrgInviteRevokeView.as_view(),      name='org-invite-revoke'),
    path('invites/',                      MyInvitesView.as_view(),            name='my-invites'),
    path('invites/<int:invite_id>/accept/',  InviteRespondView.as_view(accept=True),  name='invite-accept'),
    path('invites/<int:invite_id>/decline/', InviteRespondView.as_view(accept=False), name='invite-decline'),
    path('me/ownership/transfer/',        OrgOwnershipTransferView.as_view(), name='org-ownership-transfer'),
    path('<uuid:pk>/ownership/transfer/', OrgOwnershipTransferView.as_view(), name='admin-org-ownership-transfer'),
    path('<uuid:pk>/suspend/',            OrganizationSuspendView.as_view(),  name='org-suspend'),
    path('<uuid:pk>/activate/',           OrganizationActivateView.as_view(), name='org-activate'),
    path('<uuid:pk>/config/',             AdminOrgConfigView.as_view(),       name='admin-org-config'),
    path('<uuid:pk>/config/<str:key>/',   AdminOrgConfigKeyView.as_view(),       name='admin-org-config-key'),
    path('<uuid:pk>/',                    OrganizationDetailView.as_view(),   name='organizations-detail'),
]
