import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

_cnic_validator = RegexValidator(
    regex=r'^\d{5}-\d{7}-\d$',
    message='CNIC must be in the format XXXXX-XXXXXXX-X',
)

_phone_validator = RegexValidator(
    regex=r'^\+?[0-9]{10,15}$',
    message='Phone must be 10–15 digits, optionally prefixed with +.',
)


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

    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone     = models.CharField(max_length=20, unique=True, db_index=True, validators=[_phone_validator])
    name      = models.CharField(max_length=200, blank=True, null=True)
    email     = models.EmailField(blank=True)
    role      = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    is_filer  = models.BooleanField(default=False)
    ntn       = models.CharField(max_length=20, blank=True, null=True)
    cnic      = models.CharField(max_length=15, blank=True, null=True, validators=[_cnic_validator])
    is_active = models.BooleanField(default=True)
    is_staff  = models.BooleanField(default=False)
    last_active = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = 'users'

    def __str__(self):
        return f"{self.phone} ({self.role})"


class OTPCode(models.Model):
    phone      = models.CharField(max_length=20, db_index=True)
    code       = models.CharField(max_length=6)
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