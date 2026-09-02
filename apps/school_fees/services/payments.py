import logging
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger('apps.school_fees')


class FeePaymentError(Exception):
    """Raised for invalid fee payments."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        self.code = code


def try_paystack():
    """Return the Paystack helper module if configured, else None."""
    from django.conf import settings
    if not getattr(settings, 'PAYSTACK_SECRET_KEY', ''):
        return None
    from apps.payments import paystack
    return paystack


def initialize_fee_payment(account, amount, email, callback_url):
    """Initialize a Paystack payment for a student's outstanding balance."""
    paystack = try_paystack()
    if paystack is None:
        raise FeePaymentError('Online payments are not configured. Please pay at the office.')

    outstanding = account.outstanding_balance
    if amount > outstanding + Decimal('0.0001'):
        raise FeePaymentError('Amount exceeds the outstanding balance.')

    reference = f"FEE-{account.account_number}-{timezone.now():%Y%m%d%H%M%S}"
    return paystack.initialize_payment(
        amount=amount,
        email=email or 'student@example.com',
        reference=reference,
        callback_url=callback_url,
        metadata={
            'fee_account_id': account.pk,
            'student_id': account.student_id,
            'module': 'school_fees',
        },
    )


def _record_fee_payment(account, amount, payment_method, reference,
                        recorded_by, is_online=False, paystack_reference='', note=''):
    """Create a FeePayment, update the account balance, and return payment."""
    from django.db import transaction as db_transaction
    from .reminders import send_payment_confirmation, send_fully_paid_notification

    amount = Decimal(str(amount))
    if amount <= 0:
        raise FeePaymentError('Payment amount must be greater than zero.')
    if amount > account.outstanding_balance + Decimal('0.0001'):
        raise FeePaymentError('Payment amount exceeds the outstanding balance.')

    previous_balance = account.outstanding_balance

    with db_transaction.atomic():
        payment = account.payments.create(
            student=account.student,
            amount=amount,
            payment_date=timezone.now().date(),
            payment_method=payment_method,
            reference=reference or '',
            academic_year=account.academic_year,
            term=account.term,
            previous_balance=previous_balance,
            remaining_balance=previous_balance - amount,
            recorded_by=recorded_by,
            is_online=is_online,
            paystack_reference=paystack_reference or '',
            note=note or '',
        )
        account.recalculate_status()

    # Notifications outside the DB transaction (best-effort)
    try:
        send_payment_confirmation(account, payment)
    except Exception as e:
        logger.exception(f"Payment confirmation SMS failed: {e}")

    if account.status == account.PaymentStatus.FULLY_PAID:
        try:
            send_fully_paid_notification(account, payment)
        except Exception as e:
            logger.exception(f"Fully-paid SMS failed: {e}")

    return payment


def record_payment(account, amount, payment_method='CASH', reference='',
                   recorded_by=None, note=''):
    """Record a staff-entered fee payment."""
    return _record_fee_payment(
        account, amount, payment_method, reference,
        recorded_by, is_online=False, note=note,
    )


def verify_and_credit(account, paystack_reference, recorded_by=None):
    """Verify a Paystack payment and credit the fee account on success."""
    paystack = try_paystack()
    if paystack is None:
        raise FeePaymentError('Online payments are not configured.')

    result = paystack.verify_payment(paystack_reference)
    if not result.get('status'):
        raise FeePaymentError(result.get('message', 'Payment verification failed.'))

    from apps.payments.models import Transaction
    exists = account.payments.filter(paystack_reference=paystack_reference).exists()
    if exists:
        return account.payments.filter(paystack_reference=paystack_reference).first()

    amount = result['amount']
    return _record_fee_payment(
        account, amount, Transaction.PaymentMethod.PAYSTACK, paystack_reference,
        recorded_by, is_online=True, paystack_reference=paystack_reference,
        note='Verified online payment via Paystack.',
    )
