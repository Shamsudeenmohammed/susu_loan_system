from django.db import models
from django.conf import settings


class SMSNotification(models.Model):
    class NotificationType(models.TextChoices):
        CUSTOMER_CREATED = 'CUSTOMER_CREATED', 'Customer Created'
        PHONE_VERIFIED = 'PHONE_VERIFIED', 'Phone Verified'
        CONTRIBUTION = 'CONTRIBUTION', 'Contribution'
        WITHDRAWAL_REQUEST = 'WITHDRAWAL_REQUEST', 'Withdrawal Request'
        WITHDRAWAL_APPROVED = 'WITHDRAWAL_APPROVED', 'Withdrawal Approved'
        WITHDRAWAL_REJECTED = 'WITHDRAWAL_REJECTED', 'Withdrawal Rejected'
        WITHDRAWAL_COMPLETED = 'WITHDRAWAL_COMPLETED', 'Withdrawal Completed'
        LOAN_APPLICATION = 'LOAN_APPLICATION', 'Loan Application'
        LOAN_APPROVED = 'LOAN_APPROVED', 'Loan Approved'
        LOAN_REJECTED = 'LOAN_REJECTED', 'Loan Rejected'
        LOAN_DISBURSEMENT = 'LOAN_DISBURSEMENT', 'Loan Disbursement'
        LOAN_REPAYMENT = 'LOAN_REPAYMENT', 'Loan Repayment'
        REPAYMENT_REMINDER = 'REPAYMENT_REMINDER', 'Repayment Reminder'
        GENERAL = 'GENERAL', 'General'

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENDING = 'SENDING', 'Sending'
        SENT = 'SENT', 'Sent'
        DELIVERED = 'DELIVERED', 'Delivered'
        FAILED = 'FAILED', 'Failed'

    notification_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sms_notifications'
    )
    phone_number = models.CharField(max_length=20)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        default=NotificationType.GENERAL
    )
    reference_model = models.CharField(max_length=50, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    provider = models.CharField(max_length=50, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    delivery_status = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    unique_key = models.CharField(max_length=200, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.notification_number} - {self.notification_type} - {self.status}"

    def save(self, *args, **kwargs):
        from apps.core.utils import generate_unique_number
        if not self.notification_number:
            self.notification_number = generate_unique_number('SMS')
        super().save(*args, **kwargs)
