from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class OtpSendThrottle(AnonRateThrottle):
    scope = 'otp_send'

    def get_cache_key(self, request, view):
        phone = request.data.get('phone', '')
        if phone:
            return f'throttle_otp_{phone}'
        return super().get_cache_key(request, view)


class OtpDailyThrottle(AnonRateThrottle):
    """Hard cap: 10 OTP requests per phone number per day."""
    scope = 'otp_daily'

    def get_cache_key(self, request, view):
        phone = request.data.get('phone', '')
        if phone:
            return f'throttle_otp_daily_{phone}'
        return super().get_cache_key(request, view)


class AiQueryThrottle(UserRateThrottle):
    scope = 'ai_query'


class FraudCheckThrottle(UserRateThrottle):
    scope = 'fraud_check'
