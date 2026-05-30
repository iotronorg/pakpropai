from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r'^ws/voice/stream/(?P<call_sid>[A-Za-z0-9]+)/$',
        consumers.VoiceStreamConsumer.as_asgi(),
    ),
    re_path(
        r'^ws/voice/room/(?P<org_id>[0-9a-f-]+)/$',
        consumers.VoiceAgentRoomConsumer.as_asgi(),
    ),
]
