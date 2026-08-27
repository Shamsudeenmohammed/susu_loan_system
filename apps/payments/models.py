from django.db import models
from django.conf import settings
from apps.core.utils import generate_unique_number
from decimal import Decimal


class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        SUSU_CONTRIBUTION = 'SUSU_CONTRIBUTION', 'Susu Contribution'
        WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
        LOAN_DISBURSEMENT = 'LOAN_DISBURSEMENT', 'Loan Disbursement'
        LOAN_REPAYMENT = 'LOAN_REPAYMENT', 'Loan Repayment'
        INTEREST = 'INTEREST', 'Interest'
        PENALTY = 'PENALTY', 'Penalty'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'
        REFUND = 'REFUND', 'Refund'

    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile Money'
        BANK = 'BANK', 'Bank Transfer'
        PAYSTACK = 'PAYSTACK', 'Paystack (Online)'
        OTHER = 'OTHER', 'Other'

    transaction_number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='transactions'
    )
    account = models.ForeignKey(
        'susu.SusuAccount',
        on_delete=models.PROTECT,
        related_name='transactions',
        null=True, blank=True
    )
    transaction_type = models.CharField(max_length=30, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_before = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH
    )
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_transactions'
    )
    is_reversal = models.BooleanField(default=False)
    reversed_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reversal_of'
    )
    idempotency_key = models.CharField(max_length=100, unique=True, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer', 'transaction_type']),
            models.Index(fields=['account', 'created_at']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.transaction_number} - {self.get_transaction_type_display()} - GHS {self.amount}"

    def save(self, *args, **kwargs):
        if not self.transaction_number:
            self.transaction_number = generate_unique_number('TXN')
        super().save(*args, **kwargs)


class Withdrawal(models.Model):
    class Status(models.TextChoices):
        REQUESTED = 'REQUESTED', 'Requested'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        COMPLETED = 'COMPLETED', 'Completed'

    withdrawal_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='withdrawals'
    )
    account = models.ForeignKey(
        'susu.SusuAccount',
        on_delete=models.PROTECT,
        related_name='withdrawals'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='requested_withdrawals'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_withdrawals'
    )
    review_notes = models.TextField(blank=True)
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='withdrawal_record'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.withdrawal_number} - GHS {self.amount}"

    def save(self, *args, **kwargs):
        if not self.withdrawal_number:
            self.withdrawal_number = generate_unique_number('WD')
        super().save(*args, **kwargs)
