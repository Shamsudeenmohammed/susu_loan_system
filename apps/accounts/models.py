from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
import random


class UserManager(BaseUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not username:
            raise ValueError('Username is required')
        username = username.lower()
        if email:
            email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', self.model.Role.SUPER_ADMIN)
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
        ADMIN = 'ADMIN', 'Admin'
        MANAGER = 'MANAGER', 'Manager'
        LOAN_OFFICER = 'LOAN_OFFICER', 'Loan Officer'
        CASHIER = 'CASHIER', 'Cashier'
        COLLECTOR = 'COLLECTOR', 'Collector'
        CUSTOMER = 'CUSTOMER', 'Customer'

    email = models.EmailField(blank=True, null=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    objects = UserManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        name = self.get_full_name() or self.username
        email_part = f" ({self.email})" if self.email else ""
        return f"{name}{email_part}"

    @property
    def is_staff_member(self):
        return self.role != self.Role.CUSTOMER

    @property
    def is_superuser_role(self):
        return self.role == self.Role.SUPER_ADMIN

    def has_role(self, *roles):
        return self.role in roles


class PasswordResetOTP(models.Model):
    """
    One-time password for customer password reset via SMS.
    Linked to customer phone number for verification.
    """
    phone = models.CharField(max_length=20)
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone', 'is_used']),
        ]

    def __str__(self):
        return f"OTP for {self.phone} ({'used' if self.is_used else 'active'})"

    @classmethod
    def generate_otp(cls, phone):
        """Generate a new 6-digit OTP for the given phone number."""
        # Invalidate any existing unused OTPs for this phone
        cls.objects.filter(phone=phone, is_used=False).update(is_used=True)

        # Generate 6-digit code
        otp_code = ''.join(str(random.randint(0, 9)) for _ in range(6))

        # Expires in 10 minutes
        expires_at = timezone.now() + timezone.timedelta(minutes=10)

        return cls.objects.create(
            phone=phone,
            otp_code=otp_code,
            expires_at=expires_at,
        )

    def verify(self, otp_code):
        """Verify the OTP code. Returns True if valid."""
        if self.is_used:
            return False, 'This code has already been used.'
        if timezone.now() > self.expires_at:
            self.is_used = True
            self.save(update_fields=['is_used'])
            return False, 'This code has expired. Please request a new one.'
        if self.attempts >= self.max_attempts:
            self.is_used = True
            self.save(update_fields=['is_used'])
            return False, 'Too many failed attempts. Please request a new code.'
        if self.otp_code != otp_code:
            self.attempts += 1
            self.save(update_fields=['attempts'])
            return False, 'Invalid code. Please try again.'

        # Success
        self.is_used = True
        self.save(update_fields=['is_used'])
        return True, 'Code verified successfully.'

    def is_valid(self):
        return not self.is_used and timezone.now() <= self.expires_at and self.attempts < self.max_attempts
