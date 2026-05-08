import random
import string
from datetime import timedelta
from django.utils import timezone
from .models import OTPCode, User


class OTPService:

    OTP_LENGTH      = 6
    OTP_LIFETIME    = timedelta(minutes=5)
    # MAX_PER_HOUR    = 5    # rate limit per phone
    MAX_PER_HOUR    = 100    # rate limit per phone

    @classmethod
    def generate_code(cls) -> str:
        return ''.join(random.choices(string.digits, k=cls.OTP_LENGTH))

    @classmethod
    def issue(cls, phone: str) -> OTPCode:
        # Rate limit check
        recent = OTPCode.objects.filter(
            phone=phone,
            created_at__gte=timezone.now() - timedelta(hours=1)
        ).count()
        if recent >= cls.MAX_PER_HOUR:
            raise PermissionError('Too many OTP requests. Try again in an hour.')

        code = cls.generate_code()
        return OTPCode.objects.create(
            phone=phone,
            code=code,
            expires_at=timezone.now() + cls.OTP_LIFETIME,
        )

    @classmethod
    def verify(cls, phone: str, code: str) -> User:
        otp = (OTPCode.objects
               .filter(phone=phone, is_used=False)
               .order_by('-created_at')
               .first())

        if not otp:
            raise ValueError('No OTP found. Request a new one.')

        if not otp.is_valid():
            raise ValueError('OTP expired or too many attempts.')

        if otp.code != code:
            otp.attempts += 1
            otp.save(update_fields=['attempts'])
            raise ValueError('Invalid OTP code.')

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        user, _ = User.objects.get_or_create(phone=phone, defaults={'is_active': True})
        user.last_active = timezone.now()
        user.save(update_fields=['last_active'])
        return user