from urllib.parse import parse_qs

from channels.middleware import BaseMiddleware
from asgiref.sync import sync_to_async


class JWTAuthMiddleware(BaseMiddleware):
    """
    Reads a JWT from ?token= query param or Authorization header.
    Attaches scope["user"] and scope["org_id"] on success.
    Closes with code 4001 on missing or invalid token.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await super().__call__(scope, receive, send)
            return

        token = self._extract_token(scope)
        if not token:
            await send({"type": "websocket.close", "code": 4001})
            return

        user, org_id = await self._authenticate(token)
        if user is None:
            await send({"type": "websocket.close", "code": 4001})
            return

        scope["user"] = user
        scope["org_id"] = str(org_id) if org_id else None
        await super().__call__(scope, receive, send)

    def _extract_token(self, scope) -> str | None:
        headers = dict(scope.get("headers", []))
        # 1. Authorization: Bearer header (curl / test clients)
        auth = headers.get(b"authorization", b"").decode()
        if auth.startswith("Bearer "):
            return auth[7:]
        # 2. access_token httpOnly cookie (browsers send all cookies on WS upgrade)
        cookie_header = headers.get(b"cookie", b"").decode()
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "access_token" and value:
                return value
        # 3. ?token= query param (WebsocketCommunicator tests)
        qs = scope.get("query_string", b"").decode()
        params = parse_qs(qs)
        tokens = params.get("token", [])
        return tokens[0] if tokens else None

    @sync_to_async
    def _authenticate(self, token: str):
        try:
            from rest_framework_simplejwt.tokens import AccessToken
            from django.contrib.auth import get_user_model

            access = AccessToken(token)
            user_id = access["user_id"]
            User = get_user_model()
            user = User.objects.get(id=user_id)
            org_id = self._get_org_id(user)
            return user, org_id
        except Exception:
            return None, None

    def _get_org_id(self, user):
        from apps.organizations.models import OrganizationMembership
        membership = (
            OrganizationMembership.objects
            .filter(user=user, is_active=True)
            .select_related("organization")
            .first()
        )
        return membership.organization.id if membership else None
