import pytest
from decimal import Decimal
from apps.payments.services import record_withdrawal
from apps.payments.models import Withdrawal, Transaction
from apps.susu.models import SusuAccount


@pytest.mark.django_db
class TestWithdrawals:
    def _setup_with_balance(self, customer, susu_account, cashier_user):
        from apps.payments.services import record_contribution
        record_contribution(customer, Decimal('500.00'), 'CASH', cashier_user)
        susu_account.refresh_from_db()
        return susu_account

    def test_withdrawal_sufficient_balance(self, customer, susu_account, cashier_user):
        self._setup_with_balance(customer, susu_account, cashier_user)
        w = Withdrawal.objects.create(
            customer=customer, account=susu_account,
            amount=Decimal('200.00'), requested_by=cashier_user
        )
        txn, success, error = record_withdrawal(w, cashier_user)
        assert success is True
        susu_account.refresh_from_db()
        assert susu_account.current_balance == Decimal('300.00')

    def test_withdrawal_insufficient_balance(self, customer, susu_account, cashier_user):
        self._setup_with_balance(customer, susu_account, cashier_user)
        w = Withdrawal.objects.create(
            customer=customer, account=susu_account,
            amount=Decimal('1000.00'), requested_by=cashier_user
        )
        txn, success, error = record_withdrawal(w, cashier_user)
        assert success is False
        assert 'Insufficient balance' in error

    def test_withdrawal_creates_ledger(self, customer, susu_account, cashier_user):
        self._setup_with_balance(customer, susu_account, cashier_user)
        w = Withdrawal.objects.create(
            customer=customer, account=susu_account,
            amount=Decimal('100.00'), requested_by=cashier_user
        )
        txn, success, _ = record_withdrawal(w, cashier_user)
        assert success is True
        assert txn.transaction_type == 'WITHDRAWAL'
        assert txn.balance_before == Decimal('500.00')
        assert txn.balance_after == Decimal('400.00')
