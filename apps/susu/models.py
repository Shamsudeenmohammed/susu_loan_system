from django.db import models
from django.conf import settings
from apps.core.utils import generate_unique_number
from decimal import Decimal


class SusuAccount(models.Model):
    class Frequency(models.TextChoices):
        DAILY = 'DAILY', 'Daily'
        WEEKLY = 'WEEKLY', 'Weekly'
        BIWEEKLY = 'BIWEEKLY', 'Bi-Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'
        CUSTOM = 'CUSTOM', 'Custom'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        CLOSED = 'CLOSED', 'Closed'

    account_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.CASCADE,
        related_name='susu_accounts'
    )
    contribution_frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.WEEKLY
    )
    expected_contribution = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=Decimal('0.00'),
        help_text='Expected amount per contribution'
    )
    target_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=Decimal('0.00'),
        help_text='Target savings amount'
    )
    current_balance = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=Decimal('0.00')
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INACTIVE
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='opened_susu_accounts'
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-opened_at']

    def __str__(self):
        return f"{self.account_number} - {self.customer.get_full_name()}"

    def save(self, *args, **kwargs):
        if not self.account_number:
            self.account_number = generate_unique_number('SUS')
        super().save(*args, **kwargs)

    def recalculate_balance(self):
        """Recalculate balance from ledger - the source of truth."""
        from apps.payments.models import Transaction
        transactions = Transaction.objects.filter(
            account=self
        ).order_by('created_at')

        balance = Decimal('0.00')
        for txn in transactions:
            balance = txn.balance_after

        self.current_balance = balance
        self.save(update_fields=['current_balance'])
        return balance
