from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


class SMSCampaign(models.Model):
    """A bulk SMS campaign targeting a dynamic group of customers."""

    class CampaignType(models.TextChoices):
        GENERAL_ANNOUNCEMENT = 'GENERAL_ANNOUNCEMENT', 'General Announcement'
        REPAYMENT_REMINDER = 'REPAYMENT_REMINDER', 'Repayment Reminder'
        CONTRIBUTION_REMINDER = 'CONTRIBUTION_REMINDER', 'Contribution Reminder'
        OVERDUE_REPAYMENT_REMINDER = 'OVERDUE_REPAYMENT_REMINDER', 'Overdue Repayment Reminder'
        LOAN_NOTIFICATION = 'LOAN_NOTIFICATION', 'Loan Notification'
        ACCOUNT_APPROVAL = 'ACCOUNT_APPROVAL', 'Account Approval'
        ACCOUNT_ACTIVATION = 'ACCOUNT_ACTIVATION', 'Account Activation'
        SUSU_ACTIVATION = 'SUSU_ACTIVATION', 'Contribution/Susu Activation'
        PAYMENT_CONFIRMATION = 'PAYMENT_CONFIRMATION', 'Payment Confirmation'
        CUSTOM_MESSAGE = 'CUSTOM_MESSAGE', 'Custom Message'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        SENDING = 'SENDING', 'Sending'
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIAL = 'PARTIAL', 'Partially Delivered'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class Trigger(models.TextChoices):
        SEND_NOW = 'SEND_NOW', 'Send Now'
        SCHEDULE = 'SCHEDULE', 'Schedule for Later'

    class TargetGroup(models.TextChoices):
        ALL_ACTIVE = 'ALL_ACTIVE', 'All Active Customers'
        ACTIVE_LOANS = 'ACTIVE_LOANS', 'Customers with Active Loans'
        OUTSTANDING_REPAYMENTS = 'OUTSTANDING_REPAYMENTS', 'Customers with Outstanding Repayments'
        OVERDUE_REPAYMENTS = 'OVERDUE_REPAYMENTS', 'Customers with Overdue Repayments'
        SUSU_ACCOUNTS = 'SUSU_ACCOUNTS', 'Customers with Contributions/Susu Accounts'
        DUE_CONTRIBUTIONS = 'DUE_CONTRIBUTIONS', 'Customers with Due Contributions'
        OVERDUE_CONTRIBUTIONS = 'OVERDUE_CONTRIBUTIONS', 'Customers with Overdue Contributions'
        RECENTLY_APPROVED = 'RECENTLY_APPROVED', 'Recently Approved Customers'
        RECENTLY_ACTIVATED = 'RECENTLY_ACTIVATED', 'Recently Activated Customers'
        MANUAL_SELECTION = 'MANUAL_SELECTION', 'Manually Selected Customers'

    name = models.CharField(max_length=200)
    campaign_type = models.CharField(max_length=30, choices=CampaignType.choices)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    trigger = models.CharField(
        max_length=20, choices=Trigger.choices, default=Trigger.SEND_NOW
    )
    target_group = models.CharField(max_length=40, choices=TargetGroup.choices)
    # Human-readable label of the resolved target for display/audit
    target_label = models.CharField(max_length=200, blank=True)
    filters = models.JSONField(default=dict, blank=True)
    manual_customer_ids = models.JSONField(default=list, blank=True)

    # Counts (resolved/stored at send time)
    recipient_count = models.PositiveIntegerField(default=0)
    valid_phone_count = models.PositiveIntegerField(default=0)
    missing_phone_count = models.PositiveIntegerField(default=0)
    excluded_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    pending_count = models.PositiveIntegerField(default=0)
    sms_units = models.PositiveIntegerField(default=0)

    scheduled_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Unique identifier to guard against accidental duplicate submissions/resends
    uid = models.CharField(max_length=64, unique=True, editable=False, null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_sms_campaigns'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'SMS Campaign'
        verbose_name_plural = 'SMS Campaigns'

    def __str__(self):
        return f"{self.name} ({self.get_campaign_type_display()})"

    @property
    def is_sendable(self):
        return self.status in (self.Status.DRAFT, self.Status.SCHEDULED, self.Status.FAILED)

    @property
    def successful_count(self):
        return self.sent_count

    def refresh_statistics(self, lock=True):
        """Recompute delivery statistics from the message log."""
        from django.db.models import Count
        from django.db import transaction
        stats = self.message_logs.aggregate(
            sent=Count('id', filter=models.Q(status__in=['SENT', 'DELIVERED'])),
            delivered=Count('id', filter=models.Q(status='DELIVERED')),
            failed=Count('id', filter=models.Q(status='FAILED')),
            rejected=Count('id', filter=models.Q(status='REJECTED')),
            pending=Count('id', filter=models.Q(status__in=['QUEUED', 'SENDING', 'CANCELLED'])),
        )
        try:
            with transaction.atomic():
                c = SMSCampaign.objects.select_for_update().get(pk=self.pk)
                c.sent_count = stats['sent']
                c.delivered_count = stats['delivered']
                c.failed_count = stats['failed'] + stats['rejected']
                c.pending_count = stats['pending']
                if c.pending_count == 0:
                    if c.failed_count > 0 and c.sent_count > 0:
                        c.status = SMSCampaign.Status.PARTIAL
                    elif c.failed_count > 0:
                        c.status = SMSCampaign.Status.FAILED
                    else:
                        c.status = SMSCampaign.Status.COMPLETED
                    c.completed_at = timezone.now()
                c.save()
                return c
        except Exception:
            return self


class SMSMessageLog(models.Model):
    """Per-recipient log entry for a single campaign message."""

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENDING = 'SENDING', 'Sending'
        SENT = 'SENT', 'Sent'
        DELIVERED = 'DELIVERED', 'Delivered'
        FAILED = 'FAILED', 'Failed'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'

    campaign = models.ForeignKey(
        SMSCampaign,
        on_delete=models.CASCADE,
        related_name='message_logs'
    )
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='campaign_message_logs'
    )
    phone_number = models.CharField(max_length=20, blank=True)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    provider_message_id = models.CharField(max_length=100, blank=True)
    delivery_status = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    sms_units = models.PositiveIntegerField(default=1)
    retry_count = models.PositiveIntegerField(default=0)
    unique_key = models.CharField(max_length=200, blank=True, db_index=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Campaign SMS Log'
        verbose_name_plural = 'Campaign SMS Logs'

    def __str__(self):
        return f"{self.campaign_id} - {self.phone_number} - {self.status}"


class SMSTemplate(models.Model):
    """Reusable SMS message template with personalization placeholders."""

    name = models.CharField(max_length=200)
    campaign_type = models.CharField(
        max_length=30, choices=SMSCampaign.CampaignType.choices
    )
    message = models.TextField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_sms_templates'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'SMS Template'
        verbose_name_plural = 'SMS Templates'

    def __str__(self):
        return self.name
