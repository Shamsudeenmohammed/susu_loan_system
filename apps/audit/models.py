from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    class ActionType(models.TextChoices):
        LOGIN = 'LOGIN', 'Login'
        LOGOUT = 'LOGOUT', 'Logout'
        CUSTOMER_CREATED = 'CUSTOMER_CREATED', 'Customer Created'
        CUSTOMER_UPDATED = 'CUSTOMER_UPDATED', 'Customer Updated'
        CUSTOMER_APPROVED = 'CUSTOMER_APPROVED', 'Customer Approved'
        CUSTOMER_REJECTED = 'CUSTOMER_REJECTED', 'Customer Rejected'
        SUSU_ACCOUNT_ACTIVATED = 'SUSU_ACCOUNT_ACTIVATED', 'Susu Account Activated'
        CONTRIBUTION_CREATED = 'CONTRIBUTION_CREATED', 'Contribution Created'
        WITHDRAWAL_CREATED = 'WITHDRAWAL_CREATED', 'Withdrawal Created'
        WITHDRAWAL_APPROVED = 'WITHDRAWAL_APPROVED', 'Withdrawal Approved'
        WITHDRAWAL_REJECTED = 'WITHDRAWAL_REJECTED', 'Withdrawal Rejected'
        LOAN_CREATED = 'LOAN_CREATED', 'Loan Created'
        LOAN_APPROVED = 'LOAN_APPROVED', 'Loan Approved'
        LOAN_REJECTED = 'LOAN_REJECTED', 'Loan Rejected'
        LOAN_DISBURSED = 'LOAN_DISBURSED', 'Loan Disbursed'
        REPAYMENT_CREATED = 'REPAYMENT_CREATED', 'Repayment Created'
        USER_CREATED = 'USER_CREATED', 'User Created'
        USER_PERMISSION_CHANGED = 'USER_PERMISSION_CHANGED', 'User Permission Changed'
        SMS_SENT = 'SMS_SENT', 'SMS Sent'
        SMS_FAILED = 'SMS_FAILED', 'SMS Failed'
        PASSWORD_CHANGED = 'PASSWORD_CHANGED', 'Password Changed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=30, choices=ActionType.choices)
    description = models.TextField()
    object_type = models.CharField(max_length=50, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['object_type', 'object_id']),
        ]

    def __str__(self):
        user_str = self.user.email if self.user else 'System'
        return f"{user_str} - {self.action} - {self.created_at}"

    @classmethod
    def log(cls, action, description, user=None, object_type='', object_id=None, ip_address=None):
        return cls.objects.create(
            user=user,
            action=action,
            description=description,
            object_type=object_type,
            object_id=object_id,
            ip_address=ip_address,
        )
