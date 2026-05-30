from django.urls import path
from . import views

urlpatterns = [
    path('webhook/inbound/',                      views.VoiceInboundWebhookView.as_view(),   name='voice-inbound-webhook'),
    path('calls/initiate/',                       views.VoiceOutboundInitiateView.as_view(), name='voice-initiate'),
    path('calls/',                                views.VoiceCallListView.as_view(),          name='voice-call-list'),
    path('calls/<str:call_sid>/',                 views.VoiceCallDetailView.as_view(),        name='voice-call-detail'),
    path('calls/<str:call_sid>/barge-in/',        views.VoiceBargeInView.as_view(),           name='voice-barge-in'),
    path('config/',                               views.OrgVoiceConfigView.as_view(),         name='voice-config'),
]
