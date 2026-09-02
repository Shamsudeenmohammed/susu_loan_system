import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.payments.models import Transaction, Withdrawal
from apps.payments.services import record_contribution, record_withdrawal
from apps.loans.models import LoanProduct, Loan, RepaymentSchedule, LoanRepayment

User = get_user_model()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username='admin_test',
        email='admin@test.com',
        password='testpass123',
        first_name='Admin',
        last_name='User',
        role='SUPER_ADMIN',
        is_staff=True,
    )


@pytest.fixture
def cashier_user(db):
    return User.objects.create_user(
        username='cashier_test',
        email='cashier@test.com',
        password='testpass123',
        first_name='Cashier',
        last_name='User',
        role='CASHIER',
    )


@pytest.fixture
def customer_user(db):
    return User.objects.create_user(
        username='customer_test',
        email='customer@test.com',
        password='testpass123',
        first_name='John',
        last_name='Customer',
        role='CUSTOMER',
    )


@pytest.fixture
def customer(db, customer_user):
    return Customer.objects.create(
        user=customer_user,
        first_name='John',
        last_name='Customer',
        phone='0241234567',
        status='ACTIVE',
        registered_by=customer_user,
    )


@pytest.fixture
def susu_account(db, customer, cashier_user):
    return SusuAccount.objects.create(
        customer=customer,
        contribution_frequency='WEEKLY',
        expected_contribution=Decimal('100.00'),
        target_amount=Decimal('5000.00'),
        status='ACTIVE',
        opened_by=cashier_user,
    )


@pytest.fixture
def loan_product(db):
    return LoanProduct.objects.create(
        name='Personal Loan',
        code='PL001',
        min_amount=Decimal('500.00'),
        max_amount=Decimal('10000.00'),
        interest_rate=Decimal('24.00'),
        interest_method='FLAT',
        min_term=3,
        max_term=12,
        repayment_frequency='MONTHLY',
        processing_fee_percentage=Decimal('2.00'),
        late_payment_penalty=Decimal('50.00'),
        is_active=True,
    )
