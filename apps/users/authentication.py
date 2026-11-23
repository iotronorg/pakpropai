from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTCookieOrHeaderAuthentication(JWTAuthentication):
    """
    Reads the access token from the 'access_token' httpOnly cookie first,
    enforcing CSRF validation for that path (mirrors SessionAuthentication).
    Falls back to the standard Authorization: Bearer <token> header, which
    is inherently CSRF-safe (cross-site scripts cannot set custom headers).
    """

    # T-1011: reject tokens that skipped the second factor (apps.users.mfa).
    mfa_strict = True

    def authenticate(self, request):
        result = self._authenticate(request)
        if result is None:
            return None
        user, token = result
        from .mfa import token_acceptable
        if not token_acceptable(user, token):
            if not self.mfa_strict:
                return None                                   # treated as anonymous
            raise exceptions.AuthenticationFailed('Multi-factor authentication required. Log in again.',
                                                  code='mfa_required')
        return result

    def _authenticate(self, request):
        raw_token = request.COOKIES.get('access_token')
        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token.encode())
                user = self.get_user(validated_token)
                self._enforce_csrf(request)
                return user, validated_token
            except exceptions.AuthenticationFailed:
                raise
            except Exception:
                pass
        return super().authenticate(request)


class MfaLenientAuthentication(JWTCookieOrHeaderAuthentication):
    """For MFA enrolment endpoints: a token lacking the second factor counts as anonymous."""
    mfa_strict = False

    @staticmethod
    def _enforce_csrf(request):
        """Raise PermissionDenied if the CSRF check fails."""
        from django.middleware.csrf import CsrfViewMiddleware
        middleware = CsrfViewMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        response = middleware.process_view(request, None, (), {})
        if response:
            raise exceptions.PermissionDenied("CSRF check failed.")
