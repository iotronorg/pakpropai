import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from apps.whatsapp.middleware import JWTAuthMiddleware
from apps.whatsapp import routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(URLRouter(routing.websocket_urlpatterns))
    ),
})
