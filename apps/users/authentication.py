from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTCookieOrHeaderAuthentication(JWTAuthentication):
    """
    Reads the access token from the 'access_token' httpOnly cookie first.
    Falls back to the standard Authorization: Bearer <token> header.
    This allows the frontend to use cookies without touching localStorage.
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get('access_token')
        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token.encode())
                return self.get_user(validated_token), validated_token
            except Exception:
                pass
        return super().authenticate(request)
