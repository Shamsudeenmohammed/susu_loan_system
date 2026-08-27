import pytest
from decimal import Decimal
from django.utils import timezone
from apps.loans.models import Loan, LoanProduct, RepaymentSchedule, LoanRepayment
from apps.loans.services import calculate_repayment_schedule, apply_repayment
from apps.payments.models import Transaction


@pytest.mark.django_db
class TestLoanProducts:
    def test_create_product(self):
        p = LoanProduct.objects.create(
            name='Personal Loan', code='PL001',
            min_amount=Decimal('500'), max_amount=Decimal('10000'),
            interest_rate=Decimal('24'), interest_method='FLAT',
            min_term=3, max_term=12, repayment_frequency='MONTHLY',
            is_active=True
        )
        assert p.is_active

    def test_calculate_flat_interest(self):
        p = LoanProduct.objects.create(
            name='PL', code='PL', min_amount=Decimal('500'),
            max_amount=Decimal('10000'), interest_rate=Decimal('24'),
            interest_method='FLAT', min_term=3, max_term=12,
            repayment_frequency='MONTHLY', is_active=True
        )
        interest = p.calculate_total_interest(Decimal('1000'), 6)
        assert interest == Decimal('120.00')

    def test_calculate_reducing_interest(self):
        p = LoanProduct.objects.create(
            name='RL', code='RL', min_amount=Decimal('500'),
            max_amount=Decimal('10000'), interest_rate=Decimal('24'),
            interest_method='REDUCING_BALANCE', min_term=3, max_term=12,
            repayment_frequency='MONTHLY', is_active=True
        )
        interest = p.calculate_total_interest(Decimal('1000'), 6)
        assert interest > Decimal('0')


@pytest.mark.django_db
class TestLoans:
    def test_create_loan(self, customer, loan_product, admin_user):
        loan = Loan.objects.create(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('5000'),
            term_months=6, status='SUBMITTED',
            submitted_by=admin_user,
        )
        assert loan.loan_number.startswith('LN-')

    def test_calculate_financials(self, customer, loan_product, admin_user):
        loan = Loan(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('5000'), term_months=6,
        )
        loan.calculate_financials()
        assert loan.interest_amount > Decimal('0')
        assert loan.total_amount == loan.principal_amount + loan.interest_amount
        assert loan.processing_fee > Decimal('0')

    def test_repayment_schedule_generation(self, customer, loan_product, admin_user):
        loan = Loan.objects.create(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('5000'), term_months=6,
            status='APPROVED', submitted_by=admin_user,
            approved_by=admin_user,
        )
        loan.calculate_financials()
        loan.outstanding_balance = loan.total_amount
        loan.disbursement_date = timezone.now()
        loan.save()

        schedule = calculate_repayment_schedule(loan)
        assert len(schedule) == 6
        assert all(s['total_due'] > Decimal('0') for s in schedule)

    def test_repayment_apply(self, customer, loan_product, admin_user):
        loan = Loan.objects.create(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('5000'), term_months=6,
            status='ACTIVE', submitted_by=admin_user,
        )
        loan.calculate_financials()
        loan.outstanding_balance = loan.total_amount
        loan.save()

        installment = RepaymentSchedule.objects.create(
            loan=loan, installment_number=1,
            due_date=timezone.now().date(),
            principal_due=Decimal('1000'),
            interest_due=Decimal('200'),
            total_due=Decimal('1200'),
            remaining_balance=loan.total_amount - Decimal('1200'),
        )

        installment, excess = apply_repayment(loan, installment, Decimal('1200'))
        assert installment.status == 'PAID'
        assert excess == Decimal('0.00')

    def test_partial_repayment(self, customer, loan_product, admin_user):
        loan = Loan.objects.create(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('5000'), term_months=6,
            status='ACTIVE', submitted_by=admin_user,
        )
        installment = RepaymentSchedule.objects.create(
            loan=loan, installment_number=1,
            due_date=timezone.now().date(),
            principal_due=Decimal('1000'),
            interest_due=Decimal('200'),
            total_due=Decimal('1200'),
            remaining_balance=Decimal('6000'),
        )

        installment, excess = apply_repayment(loan, installment, Decimal('500'))
        assert installment.status == 'PARTIALLY_PAID'
        assert installment.amount_paid == Decimal('500')
        assert excess == Decimal('0.00')
