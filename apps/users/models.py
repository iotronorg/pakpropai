import re
import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

_phone_validator = RegexValidator(
    regex=r'^\+[0-9]{7,15}$',
    message='Phone must be in E.164 format: + followed by 7–15 digits, e.g. +923001234567.',
)

_PK_CNIC_RE = re.compile(r'^\d{5}-\d{7}-\d$')


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError('Phone number is required')
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        CLIENT    = 'client',    'Client'
        AGENT     = 'agent',     'Agent'
        DEVELOPER = 'developer', 'Developer'
        ADMIN     = 'admin',     'Admin'

    class PlatformRole(models.TextChoices):
        SUPER_ADMIN      = 'super_admin',      'Super Admin'
        OPS_ADMIN        = 'ops_admin',        'Operations Admin'
        AI_ADMIN         = 'ai_admin',         'AI Admin'
        COMPLIANCE_ADMIN = 'compliance_admin', 'Compliance Admin'
        BILLING_ADMIN    = 'billing_admin',    'Billing Admin'
        SUPPORT_ADMIN    = 'support_admin',    'Support Admin'

    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone     = models.CharField(max_length=20, unique=True, db_index=True, validators=[_phone_validator])
    name      = models.CharField(max_length=200, blank=True, null=True)
    email     = models.EmailField(blank=True)
    role      = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    platform_role = models.CharField(
        max_length=30,
        choices=PlatformRole.choices,
        blank=True,
        null=True,
        help_text='Platform-level sub-role. Only set for role=admin users.',
    )
    is_filer  = models.BooleanField(default=False)
    ntn       = models.CharField(max_length=20, blank=True, null=True)
    cnic      = models.CharField(max_length=30, blank=True, null=True,
                    help_text='National ID number (format varies by country)')
    is_active = models.BooleanField(default=True)
    is_staff  = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)
    last_active = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = 'users'

    def clean(self):
        # Validate CNIC only for Pakistani phone numbers (prefix +92 or 03).
        # For other markets, any national ID format is accepted.
        if self.cnic and (
            str(self.phone).startswith('+92') or str(self.phone).startswith('03')
        ):
            if not _PK_CNIC_RE.match(self.cnic):
                raise ValidationError({'cnic': 'Pakistan CNIC must be in the format XXXXX-XXXXXXX-X'})

        # Validate platform_role: only set for admin users
        if self.platform_role and self.role != 'admin':
            raise ValidationError({'platform_role': 'platform_role may only be set for admin users.'})

    def __str__(self):
        return f"{self.phone} ({self.role})"


class OTPCode(models.Model):
    class Purpose(models.TextChoices):
        OTP_LOGIN           = 'otp_login',           'OTP Login'
        REGISTRATION_VERIFY = 'registration_verify', 'Registration Verify'
        PASSWORD_RESET      = 'password_reset',      'Password Reset'

    phone      = models.CharField(max_length=20, db_index=True)
    code       = models.CharField(max_length=6)
    purpose    = models.CharField(
        max_length=30,
        choices=Purpose.choices,
        default=Purpose.OTP_LOGIN,
    )
    is_used    = models.BooleanField(default=False)
    attempts   = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otp_codes'
        ordering = ['-created_at']

    def is_valid(self):
        return (
            not self.is_used
            and self.attempts < 5
            and timezone.now() < self.expires_at
        )

    def __str__(self):
        return f"OTP for {self.phone} — used={self.is_used}"