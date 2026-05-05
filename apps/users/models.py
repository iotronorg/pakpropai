

# Create your models here.
import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):

    def create_user(self, phone, name=None, password=None, **extra_fields):
        if not phone:
            raise ValueError('Phone number is required')
        user = self.model(phone=phone, name=name, **extra_fields)
        user.set_unusable_password()  # OTP-only login — no password
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        user = self.model(phone=phone, name=name, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):

    class Role(models.TextChoices):
        USER      = 'user',      'User'
        AGENT     = 'agent',     'Agent'
        DEVELOPER = 'developer', 'Developer'
        ADMIN     = 'admin',     'Admin'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone      = models.CharField(max_length=20, unique=True)
    name       = models.CharField(max_length=200, blank=True, null=True)
    role       = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    is_filer   = models.BooleanField(default=False)
    ntn        = models.CharField(max_length=20, blank=True, null=True)
    cnic       = models.CharField(max_length=15, blank=True, null=True)
    is_active  = models.BooleanField(default=True)
    is_staff   = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    USERNAME_FIELD  = 'phone'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.name or 'Unnamed'} ({self.phone})"