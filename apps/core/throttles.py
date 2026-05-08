from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class OtpSendThrottle(AnonRateThrottle):
    scope = 'otp_send'

    def get_cache_key(self, request, view):
        # Key by phone number so the limit is per-phone, not per-IP
        phone = request.data.get('phone', '')
        if phone:
            return f'throttle_otp_{phone}'
        return super().get_cache_key(request, view)


class AiQueryThrottle(UserRateThrottle):
    scope = 'ai_query'


class FraudCheckThrottle(UserRateThrottle):
    scope = 'fraud_check'
