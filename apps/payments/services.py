from decimal import Decimal
from django.db import transaction as db_transaction
from apps.core.utils import generate_unique_number
import logging

logger = logging.getLogger('apps.payments')


def record_contribution(customer, amount, payment_method, created_by, reference='', notes='', account=None):
    """
    Record a susu contribution with full transaction integrity.
    If account is provided, uses that specific account. Otherwise finds first ACTIVE.
    Returns (transaction_record, success, error_message).
    """
    from apps.susu.models import SusuAccount
    from apps.payments.models import Transaction

    if amount <= Decimal('0.00'):
        return None, False, 'Amount must be greater than zero.'

    with db_transaction.atomic():
        if account:
            susu_account = SusuAccount.objects.select_for_update().get(pk=account.pk, status='ACTIVE')
        else:
            susu_account = SusuAccount.objects.select_for_update().filter(
                customer=customer, status='ACTIVE'
            ).first()

        if not susu_account:
            return None, False, 'No active susu account found for this customer.'

        balance_before = susu_account.current_balance
        balance_after = balance_before + amount

        txn = Transaction.objects.create(
            customer=customer,
            account=susu_account,
            transaction_type=Transaction.TransactionType.SUSU_CONTRIBUTION,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_method=payment_method,
            reference=reference,
            description=notes or f'Susu contribution of GHS {amount:.2f}',
            created_by=created_by,
        )

        susu_account.current_balance = balance_after
        susu_account.save(update_fields=['current_balance'])

    logger.info(
        "record_contribution OK. txn=%s customer=%s amount=%.2f balance_before=%.2f balance_after=%.2f method=%s",
        txn.transaction_number, customer.pk, float(amount), float(balance_before), float(balance_after), payment_method,
    )
    return txn, True, None


def record_withdrawal(withdrawal_record, approved_by):
    """
    Process an approved withdrawal.
    Returns (transaction_record, success, error_message).
    """
    from apps.susu.models import SusuAccount
    from apps.payments.models import Transaction

    with db_transaction.atomic():
        susu_account = SusuAccount.objects.select_for_update().get(
            pk=withdrawal_record.account.pk
        )

        if susu_account.current_balance < withdrawal_record.amount:
            logger.warning(
                "withdrawal insufficient_balance. account=%s balance=%.2f requested=%.2f",
                susu_account.pk, float(susu_account.current_balance), float(withdrawal_record.amount),
            )
            return None, False, 'Insufficient balance.'

        balance_before = susu_account.current_balance
        balance_after = balance_before - withdrawal_record.amount

        txn = Transaction.objects.create(
            customer=withdrawal_record.customer,
            account=susu_account,
            transaction_type=Transaction.TransactionType.WITHDRAWAL,
            amount=withdrawal_record.amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_method=Transaction.PaymentMethod.CASH,
            reference=withdrawal_record.withdrawal_number,
            description=f'Withdrawal: {withdrawal_record.reason}',
            created_by=approved_by,
        )

        susu_account.current_balance = balance_after
        susu_account.save(update_fields=['current_balance'])

        withdrawal_record.transaction = txn
        withdrawal_record.status = 'COMPLETED'
        withdrawal_record.save(update_fields=['transaction', 'status'])

    logger.info(
        "record_withdrawal OK. txn=%s customer=%s amount=%.2f balance_before=%.2f balance_after=%.2f",
        txn.transaction_number, withdrawal_record.customer.pk, float(withdrawal_record.amount),
        float(balance_before), float(balance_after),
    )
    return txn, True, None
