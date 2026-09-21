import pytest
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import Customer
from apps.customers.services import delete_customer_cascade
from apps.loans.models import Loan, RepaymentSchedule, LoanRepayment, EligibilityAudit, LoanPolicy
from apps.payments.models import Transaction, Withdrawal
from apps.payments.services import record_contribution, record_withdrawal
from apps.susu.models import SusuAccount


def _build_customer_financial_history(customer, susu_account, loan_product, admin_user):
    """Create contributions, a completed withdrawal, a loan with a repayment and an eligibility audit."""
    txn_c, ok, _ = record_contribution(
        customer=customer,
        amount=Decimal('500.00'),
        payment_method=Transaction.PaymentMethod.CASH,
        created_by=admin_user,
        account=susu_account,
    )
    assert ok

    wd = Withdrawal.objects.create(
        customer=customer,
        account=susu_account,
        amount=Decimal('100.00'),
        status=Withdrawal.Status.APPROVED,
        requested_by=admin_user,
    )
    txn_w, ok, _ = record_withdrawal(wd, admin_user)
    assert ok

    loan = Loan.objects.create(
        customer=customer,
        loan_product=loan_product,
        principal_amount=Decimal('1000.00'),
        term_months=3,
        status='ACTIVE',
        submitted_by=admin_user,
    )
    loan.calculate_financials()
    loan.outstanding_balance = loan.total_amount
    loan.save()

    schedule = RepaymentSchedule.objects.create(
        loan=loan,
        installment_number=1,
        due_date=timezone.now().date(),
        principal_due=Decimal('300.00'),
        interest_due=Decimal('20.00'),
        total_due=Decimal('320.00'),
        remaining_balance=loan.total_amount - Decimal('320.00'),
    )
    LoanRepayment.objects.create(
        loan=loan,
        installment=schedule,
        amount=Decimal('320.00'),
        payment_method='CASH',
        recorded_by=admin_user,
    )
    EligibilityAudit.objects.create(
        customer=customer,
        policy=LoanPolicy.get_active(),
        eligible=True,
        eligibility_score=80,
        maximum_loan_amount=Decimal('1000.00'),
    )
    return txn_c, txn_w, loan


@pytest.mark.django_db
class TestDeleteCustomerCascade:
    def test_cascade_delete_removes_all_related_records(self, customer, susu_account, loan_product, admin_user, customer_user):
        txn_c, txn_w, loan = _build_customer_financial_history(
            customer, susu_account, loan_product, admin_user
        )
        customer_pk = customer.pk
        susu_pk = susu_account.pk

        deleted = delete_customer_cascade(customer, actor=admin_user)

        assert deleted['transactions'] == 2
        assert deleted['withdrawals'] == 1
        assert deleted['accounts'] == 1
        assert deleted['loans'] == 1
        assert deleted['repayments'] == 1
        assert deleted['schedules'] == 1
        assert deleted['eligibility_audits'] == 1

        assert not Customer.objects.filter(pk=customer_pk).exists()
        assert not SusuAccount.objects.filter(pk=susu_pk).exists()
        assert not Transaction.objects.filter(pk__in=[txn_c.pk, txn_w.pk]).exists()
        assert not Withdrawal.objects.filter(customer_id=customer_pk).exists()
        assert not Loan.objects.filter(pk=loan.pk).exists()
        assert not LoanRepayment.objects.filter(loan=loan).exists()
        assert not RepaymentSchedule.objects.filter(loan=loan).exists()
        assert not EligibilityAudit.objects.filter(customer_id=customer_pk).exists()
        # The customer's own records are gone, but the user account is left
        # intact (only the customer profile is removed).
        assert customer_user.__class__.objects.filter(pk=customer_user.pk).exists()

    def test_cascade_delete_customer_without_financials(self, customer, admin_user):
        deleted = delete_customer_cascade(customer, actor=admin_user)
        assert deleted['transactions'] == 0
        assert deleted['accounts'] == 0
        assert not Customer.objects.filter(pk=customer.pk).exists()


@pytest.mark.django_db
class TestCustomerDeleteView:
    def test_admin_can_delete_customer_with_financial_history(self, client, customer, susu_account, loan_product, admin_user):
        _build_customer_financial_history(customer, susu_account, loan_product, admin_user)

        client.force_login(admin_user)
        response = client.post(reverse('customer_delete', args=[customer.pk]))

        assert response.status_code == 302
        assert response.url == reverse('customer_list')
        assert not Customer.objects.filter(pk=customer.pk).exists()

    def test_delete_requires_post(self, client, customer, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse('customer_delete', args=[customer.pk]))
        assert response.status_code == 405
        assert Customer.objects.filter(pk=customer.pk).exists()