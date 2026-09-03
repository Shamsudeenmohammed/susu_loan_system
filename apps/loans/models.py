from django.db import models
from django.conf import settings
from apps.core.utils import generate_unique_number
from decimal import Decimal
from datetime import timedelta
from dateutil.relativedelta import relativedelta


class LoanProduct(models.Model):
    class InterestMethod(models.TextChoices):
        FLAT = 'FLAT', 'Flat'
        REDUCING_BALANCE = 'REDUCING_BALANCE', 'Reducing Balance'

    class RepaymentFrequency(models.TextChoices):
        DAILY = 'DAILY', 'Daily'
        WEEKLY = 'WEEKLY', 'Weekly'
        BIWEEKLY = 'BIWEEKLY', 'Bi-Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text='Annual interest rate (e.g., 24.00 for 24%)'
    )
    interest_method = models.CharField(
        max_length=20,
        choices=InterestMethod.choices,
        default=InterestMethod.FLAT
    )
    min_term = models.PositiveIntegerField(help_text='Minimum term in months')
    max_term = models.PositiveIntegerField(help_text='Maximum term in months')
    repayment_frequency = models.CharField(
        max_length=20,
        choices=RepaymentFrequency.choices,
        default=RepaymentFrequency.MONTHLY
    )
    processing_fee_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        help_text='Processing fee as percentage'
    )
    late_payment_penalty = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Fixed penalty per late payment'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def calculate_total_interest(self, principal, term_months):
        """Calculate total interest based on method."""
        rate = self.interest_rate / Decimal('100')
        if self.interest_method == self.InterestMethod.FLAT:
            annual_interest = principal * rate
            total_interest = annual_interest * Decimal(str(term_months)) / Decimal('12')
            return total_interest.quantize(Decimal('0.01'))
        else:
            total_interest = Decimal('0.00')
            balance = principal
            monthly_rate = rate / Decimal('12')
            for _ in range(term_months):
                interest = balance * monthly_rate
                total_interest += interest
                principal_payment = principal / Decimal(str(term_months))
                balance -= principal_payment
            return total_interest.quantize(Decimal('0.01'))


class Loan(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        DISBURSED = 'DISBURSED', 'Disbursed'
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'
        DEFAULTED = 'DEFAULTED', 'Defaulted'
        CANCELLED = 'CANCELLED', 'Cancelled'

    loan_number = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='loans'
    )
    loan_product = models.ForeignKey(
        LoanProduct,
        on_delete=models.PROTECT,
        related_name='loans'
    )
    principal_amount = models.DecimalField(max_digits=12, decimal_places=2)
    interest_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    processing_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    disbursement_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    outstanding_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    term_months = models.PositiveIntegerField()
    repayment_frequency = models.CharField(max_length=20, default='MONTHLY')
    purpose = models.TextField(blank=True)
    income_info = models.TextField(blank=True)
    application_date = models.DateTimeField(auto_now_add=True)
    approval_date = models.DateTimeField(null=True, blank=True)
    disbursement_date = models.DateTimeField(null=True, blank=True)
    maturity_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_loans'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_loans'
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rejected_loans'
    )
    rejection_reason = models.TextField(blank=True)
    disbursement_notes = models.TextField(blank=True)
    eligibility_snapshot = models.JSONField(default=dict, blank=True)
    transaction = models.ForeignKey(
        'payments.Transaction',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='loan_record'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.loan_number} - {self.customer.get_full_name()}"

    def save(self, *args, **kwargs):
        if not self.loan_number:
            self.loan_number = generate_unique_number('LN')
        super().save(*args, **kwargs)

    def calculate_financials(self):
        """Calculate interest, total, and processing fee."""
        self.interest_amount = self.loan_product.calculate_total_interest(
            self.principal_amount, self.term_months
        )
        self.processing_fee = (
            self.principal_amount * self.loan_product.processing_fee_percentage / Decimal('100')
        ).quantize(Decimal('0.01'))
        self.total_amount = self.principal_amount + self.interest_amount
        self.disbursement_amount = self.principal_amount - self.processing_fee

    @property
    def total_paid(self):
        return self.total_amount - self.outstanding_balance

    @property
    def repayment_count(self):
        return self.repayment_schedules.filter(status='PAID').count()

    @property
    def total_installments(self):
        return self.repayment_schedules.count()

    @property
    def completion_percentage(self):
        if self.total_amount == 0:
            return 0
        return int((self.total_paid / self.total_amount) * 100)


class RepaymentSchedule(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        PAID = 'PAID', 'Paid'
        OVERDUE = 'Overdue'

    loan = models.ForeignKey(
        Loan,
        on_delete=models.CASCADE,
        related_name='repayment_schedules'
    )
    installment_number = models.PositiveIntegerField()
    due_date = models.DateField()
    principal_due = models.DecimalField(max_digits=12, decimal_places=2)
    interest_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    penalty = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    paid_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['installment_number']
        unique_together = ['loan', 'installment_number']

    def __str__(self):
        return f"{self.loan.loan_number} - Installment {self.installment_number}"

    @property
    def is_overdue(self):
        from django.utils import timezone
        return self.due_date < timezone.now().date() and self.status != 'PAID'

    @property
    def amount_remaining(self):
        return self.total_due - self.amount_paid


class LoanRepayment(models.Model):
    repayment_number = models.CharField(max_length=20, unique=True, editable=False)
    loan = models.ForeignKey(
        Loan,
        on_delete=models.PROTECT,
        related_name='repayments'
    )
    installment = models.ForeignKey(
        RepaymentSchedule,
        on_delete=models.PROTECT,
        related_name='repayments',
        null=True, blank=True
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=[('CASH', 'Cash'), ('MOBILE_MONEY', 'Mobile Money'), ('BANK', 'Bank'), ('OTHER', 'Other')],
        default='CASH'
    )
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    transaction = models.ForeignKey(
        'payments.Transaction',
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.repayment_number} - GHS {self.amount}"

    def save(self, *args, **kwargs):
        if not self.repayment_number:
            self.repayment_number = generate_unique_number('RPY')
        super().save(*args, **kwargs)


class LoanPolicy(models.Model):
    name = models.CharField(max_length=200, default='Standard Loan Policy')
    minimum_membership_days = models.PositiveIntegerField(
        default=90,
        help_text='Minimum days since registration'
    )
    minimum_contribution_days = models.PositiveIntegerField(
        default=90,
        help_text='Minimum days of active contributing'
    )
    minimum_successful_contributions = models.PositiveIntegerField(
        default=12,
        help_text='Minimum number of verified successful contributions'
    )
    minimum_savings = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('1000.00'),
        help_text='Minimum total verified savings (GHS)'
    )
    maximum_loan_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('2.00'),
        help_text='Maximum loan amount as multiplier of savings'
    )
    maximum_active_loans = models.PositiveIntegerField(default=1)
    maximum_missed_periods = models.PositiveIntegerField(
        default=2,
        help_text='Maximum allowed missed contribution periods'
    )
    waiting_period_days = models.PositiveIntegerField(
        default=7,
        help_text='Days after registration before eligibility check'
    )
    require_kyc = models.BooleanField(default=True)
    require_good_repayment_history = models.BooleanField(default=True)
    block_overdue_customers = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-is_active', '-created_at']
        verbose_name_plural = 'Loan Policies'

    def __str__(self):
        return f"{self.name} {'(Active)' if self.is_active else '(Inactive)'}"

    def save(self, *args, **kwargs):
        if self.is_active:
            LoanPolicy.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first() or cls.objects.create(
            name='Default Loan Policy',
            is_active=True,
        )


class EligibilityAudit(models.Model):
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='eligibility_audits'
    )
    policy = models.ForeignKey(
        LoanPolicy,
        on_delete=models.PROTECT,
        related_name='eligibility_audits'
    )
    eligible = models.BooleanField(default=False)
    maximum_loan_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00')
    )
    eligibility_score = models.PositiveIntegerField(default=0)
    passed_criteria = models.JSONField(default=list, blank=True)
    failed_criteria = models.JSONField(default=list, blank=True)
    membership_months = models.PositiveIntegerField(default=0)
    required_membership_months = models.PositiveIntegerField(default=0)
    successful_contributions = models.PositiveIntegerField(default=0)
    required_contributions = models.PositiveIntegerField(default=0)
    total_savings = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    minimum_savings = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    missed_periods = models.PositiveIntegerField(default=0)
    max_missed_periods = models.PositiveIntegerField(default=0)
    active_loans = models.PositiveIntegerField(default=0)
    has_overdue = models.BooleanField(default=False)
    is_kyc_complete = models.BooleanField(default=True)
    contribution_period_months = models.PositiveIntegerField(default=0)
    required_contribution_months = models.PositiveIntegerField(default=0)
    snapshot_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        status = 'ELIGIBLE' if self.eligible else 'NOT ELIGIBLE'
        return f"{self.customer} - {status} ({self.created_at:%Y-%m-%d})"

    def to_snapshot(self):
        return {
            'customer_id': self.customer.pk,
            'customer_number': self.customer.customer_number,
            'policy_name': self.policy.name,
            'eligible': self.eligible,
            'maximum_loan_amount': str(self.maximum_loan_amount),
            'eligibility_score': self.eligibility_score,
            'membership_months': self.membership_months,
            'required_membership_months': self.required_membership_months,
            'successful_contributions': self.successful_contributions,
            'required_contributions': self.required_contributions,
            'total_savings': str(self.total_savings),
            'minimum_savings': str(self.minimum_savings),
            'missed_periods': self.missed_periods,
            'max_missed_periods': self.max_missed_periods,
            'active_loans': self.active_loans,
            'has_overdue': self.has_overdue,
            'is_kyc_complete': self.is_kyc_complete,
            'contribution_period_months': self.contribution_period_months,
            'required_contribution_months': self.required_contribution_months,
            'evaluation_date': self.created_at.isoformat(),
        }
