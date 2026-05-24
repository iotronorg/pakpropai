from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r"^ws/agent/org/(?P<org_id>[0-9a-f-]+)/$",
        consumers.AgentRoomConsumer.as_asgi(),
    ),
]
