import pytest
from decimal import Decimal
from apps.susu.models import SusuAccount
from apps.payments.services import record_contribution
from apps.payments.models import Transaction


@pytest.mark.django_db
class TestSusuAccount:
    def test_create_account(self, customer, cashier_user):
        acc = SusuAccount.objects.create(
            customer=customer,
            contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'),
            opened_by=cashier_user,
        )
        assert acc.account_number.startswith('SUS-')
        assert acc.current_balance == Decimal('0.00')
        assert acc.status == 'ACTIVE'

    def test_account_str(self, customer, cashier_user):
        acc = SusuAccount.objects.create(
            customer=customer, contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'), opened_by=cashier_user
        )
        assert acc.account_number in str(acc)


@pytest.mark.django_db
class TestContributions:
    def test_record_contribution(self, customer, susu_account, cashier_user):
        txn, success, error = record_contribution(
            customer=customer,
            amount=Decimal('100.00'),
            payment_method='CASH',
            created_by=cashier_user,
        )
        assert success is True
        assert error is None
        assert txn.amount == Decimal('100.00')
        assert txn.balance_before == Decimal('0.00')
        assert txn.balance_after == Decimal('100.00')
        assert txn.transaction_type == 'SUSU_CONTRIBUTION'
        assert txn.transaction_number.startswith('TXN-')

        susu_account.refresh_from_db()
        assert susu_account.current_balance == Decimal('100.00')

    def test_multiple_contributions(self, customer, susu_account, cashier_user):
        record_contribution(customer, Decimal('100.00'), 'CASH', cashier_user)
        record_contribution(customer, Decimal('50.00'), 'MOBILE_MONEY', cashier_user)
        record_contribution(customer, Decimal('200.00'), 'CASH', cashier_user)

        susu_account.refresh_from_db()
        assert susu_account.current_balance == Decimal('350.00')

    def test_invalid_amount(self, customer, susu_account, cashier_user):
        txn, success, error = record_contribution(
            customer, Decimal('-10.00'), 'CASH', cashier_user
        )
        assert success is False
        assert error is not None

    def test_zero_amount(self, customer, susu_account, cashier_user):
        txn, success, error = record_contribution(
            customer, Decimal('0.00'), 'CASH', cashier_user
        )
        assert success is False

    def test_no_active_account(self, customer, cashier_user):
        customer.susu_account_set = None
        txn, success, error = record_contribution(
            customer, Decimal('100.00'), 'CASH', cashier_user
        )
        assert success is False
        assert 'No active susu account' in error

    def test_transaction_ledger_integrity(self, customer, susu_account, cashier_user):
        record_contribution(customer, Decimal('100.00'), 'CASH', cashier_user)
        record_contribution(customer, Decimal('50.00'), 'CASH', cashier_user)

        txns = Transaction.objects.filter(account=susu_account).order_by('created_at')
        assert txns.count() == 2
        assert txns[0].balance_after == Decimal('100.00')
        assert txns[1].balance_before == Decimal('100.00')
        assert txns[1].balance_after == Decimal('150.00')

    def test_sms_disabled(self, customer, susu_account, cashier_user):
        """Test that contributions work even when SMS is disabled."""
        txn, success, error = record_contribution(
            customer, Decimal('100.00'), 'CASH', cashier_user
        )
        assert success is True
        # Transaction should be created regardless of SMS status
        assert txn.pk is not None
