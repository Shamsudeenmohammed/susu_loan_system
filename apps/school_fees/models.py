from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal

from apps.core.utils import generate_unique_number


class SchoolClass(models.Model):
    """A class/program within the school (e.g. Class 1, JHS 2, SHS 3)."""

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'School Classes'

    def __str__(self):
        return self.name


class AcademicYear(models.Model):
    """An academic year (e.g. 2025/2026)."""

    name = models.CharField(max_length=50, unique=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-name']

    def __str__(self):
        return self.name


class Term(models.Model):
    """A term/semester within an academic year (e.g. First Term)."""

    class TermNumber(models.IntegerChoices):
        FIRST = 1, 'First Term'
        SECOND = 2, 'Second Term'
        THIRD = 3, 'Third Term'

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='terms'
    )
    name = models.CharField(max_length=50)
    term_number = models.PositiveIntegerField(choices=TermNumber.choices, default=TermNumber.FIRST)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['academic_year', 'term_number']
        unique_together = ['academic_year', 'term_number']

    def __str__(self):
        return f"{self.academic_year.name} - {self.name}"


class Student(models.Model):
    """A student enrolled in the school."""

    student_id = models.CharField(max_length=30, unique=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name='students'
    )
    parent_name = models.CharField(max_length=200)
    parent_phone = models.CharField(max_length=20)
    parent_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_students'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['school_class', 'is_active']),
        ]

    def __str__(self):
        return f"{self.student_id} - {self.get_full_name()}"

    def save(self, *args, **kwargs):
        if not self.student_id:
            self.student_id = generate_unique_number('STU')
        super().save(*args, **kwargs)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"


class FeeCategory(models.Model):
    """A fee type/category (e.g. Tuition, Transportation, Books)."""

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Fee Categories'

    def __str__(self):
        return self.name


class FeeStructure(models.Model):
    """Fees configured for an academic year + term + class + category."""

    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='fee_structures'
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.PROTECT,
        related_name='fee_structures'
    )
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name='fee_structures'
    )
    fee_category = models.ForeignKey(
        FeeCategory,
        on_delete=models.PROTECT,
        related_name='fee_structures'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_fee_structures'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['academic_year', 'term', 'school_class', 'fee_category']
        unique_together = ['academic_year', 'term', 'school_class', 'fee_category']

    def __str__(self):
        return (
            f"{self.academic_year.name} | {self.term.name} | "
            f"{self.school_class.name} | {self.fee_category.name} | GHS {self.amount}"
        )


class StudentFeeAccount(models.Model):
    """One fee account per student per academic year + term."""

    class PaymentStatus(models.TextChoices):
        NOT_PAID = 'NOT_PAID', 'Not Paid'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        FULLY_PAID = 'FULLY_PAID', 'Fully Paid'
        OVERDUE = 'OVERDUE', 'Overdue'

    account_number = models.CharField(max_length=30, unique=True, editable=False)
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name='fee_accounts'
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='fee_accounts'
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.PROTECT,
        related_name='fee_accounts'
    )
    total_fees = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.NOT_PAID
    )
    last_payment_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-academic_year', 'student']
        unique_together = ['student', 'academic_year', 'term']

    def __str__(self):
        return (
            f"{self.account_number} - {self.student.get_full_name()} "
            f"({self.academic_year.name} {self.term.name})"
        )

    def save(self, *args, **kwargs):
        if not self.account_number:
            self.account_number = generate_unique_number('FEE')
        super().save(*args, **kwargs)

    @property
    def outstanding_balance(self):
        return self.total_fees - self.amount_paid

    @property
    def due_date(self):
        fs = FeeStructure.objects.filter(
            academic_year=self.academic_year,
            term=self.term,
            school_class=self.student.school_class,
            is_active=True,
        )
        dates = [f.due_date for f in fs if f.due_date]
        return max(dates) if dates else None

    @property
    def is_overdue(self):
        dd = self.due_date
        if dd is None or self.status == self.PaymentStatus.FULLY_PAID:
            return False
        return dd < timezone.now().date()

    def recalculate_status(self):
        """Recompute amount_paid from payments and refresh status."""
        from django.db.models import Sum
        paid = self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        self.amount_paid = paid
        if self.total_fees <= 0:
            self.status = self.PaymentStatus.FULLY_PAID
        elif self.amount_paid >= self.total_fees:
            self.status = self.PaymentStatus.FULLY_PAID
        elif self.amount_paid > 0:
            self.status = self.PaymentStatus.PARTIALLY_PAID
        else:
            self.status = self.PaymentStatus.NOT_PAID
        last = self.payments.order_by('-payment_date').first()
        self.last_payment_date = last.payment_date if last else self.last_payment_date
        self.save(update_fields=['amount_paid', 'status', 'last_payment_date', 'updated_at', 'total_fees'])
        return self.status


class FeePayment(models.Model):
    """A payment recorded against a student's fee account."""

    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile Money'
        BANK = 'BANK', 'Bank Transfer'
        PAYSTACK = 'PAYSTACK', 'Paystack (Online)'
        OTHER = 'OTHER', 'Other'

    receipt_number = models.CharField(max_length=30, unique=True, editable=False)
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name='fee_payments'
    )
    account = models.ForeignKey(
        StudentFeeAccount,
        on_delete=models.PROTECT,
        related_name='payments'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.now)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH
    )
    reference = models.CharField(max_length=100, blank=True)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='fee_payments'
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.PROTECT,
        related_name='fee_payments'
    )
    previous_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_fee_payments'
    )
    paystack_reference = models.CharField(max_length=100, blank=True, db_index=True)
    is_online = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        return f"{self.receipt_number} - GHS {self.amount}"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = generate_unique_number('REC')
        super().save(*args, **kwargs)


class ReminderTemplate(models.Model):
    """An editable SMS template for fee reminders."""

    class ReminderType(models.TextChoices):
        UPCOMING = 'UPCOMING', 'Upcoming Payment Reminder'
        DUE_DATE = 'DUE_DATE', 'Due Date Reminder'
        OVERDUE = 'OVERDUE', 'Overdue Reminder'
        PARTIAL = 'PARTIAL', 'Partial Payment Notification'
        PAYMENT_CONFIRMATION = 'PAYMENT_CONFIRMATION', 'Payment Confirmation'
        FULLY_PAID = 'FULLY_PAID', 'Fully Paid Notification'

    name = models.CharField(max_length=200)
    reminder_type = models.CharField(
        max_length=30,
        choices=ReminderType.choices,
        unique=True
    )
    message = models.TextField(
        help_text=(
            'Placeholders: {{student_name}}, {{parent_name}}, {{total_fees}}, '
            '{{amount_paid}}, {{balance}}, {{due_date}}'
        )
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['reminder_type']

    def __str__(self):
        return self.name


class ReminderLog(models.Model):
    """A record of an SMS reminder sent to a parent/guardian."""

    class Status(models.TextChoices):
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    reminder_type = models.CharField(max_length=30, choices=ReminderTemplate.ReminderType.choices)
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='reminder_logs',
        null=True, blank=True
    )
    parent_phone = models.CharField(max_length=20)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    unique_key = models.CharField(max_length=100, blank=True, db_index=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sent_fee_reminders'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_reminder_type_display()} to {self.parent_phone}"
