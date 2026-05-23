import secrets
import string
from datetime import timedelta
from django.db.models import F
from django.utils import timezone
from .models import OTPCode, User


class OTPService:

    OTP_LENGTH   = 6
    OTP_LIFETIME = timedelta(minutes=5)
    MAX_PER_HOUR = 10

    @classmethod
    def generate_code(cls) -> str:
        return ''.join(secrets.choice(string.digits) for _ in range(cls.OTP_LENGTH))

    @classmethod
    def issue(cls, phone: str, purpose: str = 'otp_login') -> OTPCode:
        recent = OTPCode.objects.filter(
            phone=phone,
            purpose=purpose,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= cls.MAX_PER_HOUR:
            raise ValueError('Too many OTP requests. Try again in an hour.')

        code = cls.generate_code()
        return OTPCode.objects.create(
            phone=phone,
            code=code,
            purpose=purpose,
            expires_at=timezone.now() + cls.OTP_LIFETIME,
        )

    @classmethod
    def verify(cls, phone: str, code: str, purpose: str = 'otp_login') -> OTPCode:
        """Validate OTP, mark used, return the OTPCode object."""
        otp = (
            OTPCode.objects
            .filter(phone=phone, is_used=False, purpose=purpose)
            .order_by('-created_at')
            .first()
        )
        if not otp:
            raise ValueError('No OTP found. Request a new one.')
        if not otp.is_valid():
            raise ValueError('OTP expired or too many attempts.')
        if otp.code != code:
            OTPCode.objects.filter(pk=otp.pk).update(attempts=F('attempts') + 1)
            raise ValueError('Invalid OTP code.')

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        return otp

    @classmethod
    def verify_login(cls, phone: str, code: str) -> User:
        """OTP login: validate OTP, auto-create user if new (backward compat)."""
        cls.verify(phone, code, purpose='otp_login')
        user, _ = User.objects.get_or_create(phone=phone, defaults={'is_active': True})
        user.last_active = timezone.now()
        user.save(update_fields=['last_active'])
        return user
