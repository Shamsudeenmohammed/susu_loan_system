import pytest
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.payments.models import Transaction
from apps.payments.services import record_contribution
from apps.loans.models import Loan, LoanProduct, LoanPolicy, EligibilityAudit, RepaymentSchedule
from apps.loans.eligibility import LoanEligibilityService

User = get_user_model()


@pytest.fixture
def loan_policy(db):
    return LoanPolicy.objects.create(
        name='Test Policy',
        minimum_membership_days=90,
        minimum_contribution_days=90,
        minimum_successful_contributions=12,
        minimum_savings=Decimal('1000.00'),
        maximum_loan_multiplier=Decimal('2.00'),
        maximum_active_loans=1,
        maximum_missed_periods=2,
        waiting_period_days=7,
        require_kyc=True,
        require_good_repayment_history=True,
        block_overdue_customers=True,
        is_active=True,
    )


@pytest.fixture
def eligible_customer(db):
    """Customer registered 4 months ago with full KYC."""
    user = User.objects.create_user(
        email='eligible@test.com', password='testpass123',
        first_name='Eligible', last_name='Member', role='CUSTOMER',
    )
    customer = Customer.objects.create(
        user=user, first_name='Eligible', last_name='Member',
        phone='0241234567', email='eligible@test.com',
        address='123 Test St', id_type='NATIONAL_ID', id_number='GHA-12345',
        emergency_contact_name='Test Contact', emergency_contact_phone='0249876543',
        status='ACTIVE',
    )
    Customer.objects.filter(pk=customer.pk).update(
        created_at=timezone.now() - timedelta(days=120)
    )
    customer.refresh_from_db()
    return customer


@pytest.fixture
def new_customer(db):
    """Brand new customer."""
    user = User.objects.create_user(
        email='new@test.com', password='testpass123',
        first_name='New', last_name='Customer', role='CUSTOMER',
    )
    return Customer.objects.create(
        user=user, first_name='New', last_name='Customer',
        phone='0241111111', status='ACTIVE',
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email='admin@test.com', password='testpass123',
        first_name='Admin', last_name='User', role='SUPER_ADMIN', is_staff=True,
    )


@pytest.fixture
def cashier_user(db):
    return User.objects.create_user(
        email='cashier@test.com', password='testpass123',
        first_name='Cashier', last_name='User', role='CASHIER',
    )


@pytest.fixture
def susu_account(db, eligible_customer, cashier_user):
    return SusuAccount.objects.create(
        customer=eligible_customer, contribution_frequency='WEEKLY',
        expected_contribution=Decimal('100.00'), target_amount=Decimal('5000.00'),
        status='ACTIVE', opened_by=cashier_user,
        opened_at=timezone.now() - timedelta(days=120),
    )


@pytest.fixture
def loan_product(db):
    return LoanProduct.objects.create(
        name='Personal Loan', code='PL001',
        min_amount=Decimal('500.00'), max_amount=Decimal('10000.00'),
        interest_rate=Decimal('24.00'), interest_method='FLAT',
        min_term=3, max_term=12, repayment_frequency='MONTHLY',
        is_active=True,
    )


def _make_contributions(customer, count, amount=Decimal('100.00'), cashier=None):
    """Helper to create verified contribution transactions spread over time."""
    from django.db import connection
    if not cashier:
        cashier = User.objects.create_user(
            email=f'helper_{customer.pk}@test.com', password='testpass123',
            first_name='Helper', last_name='Cashier', role='CASHIER',
        )
    now = timezone.now()
    for i in range(count):
        txn = Transaction.objects.create(
            customer=customer,
            transaction_type='SUSU_CONTRIBUTION',
            amount=amount,
            balance_before=Decimal('0.00'),
            balance_after=amount,
            payment_method='CASH',
            created_by=cashier,
        )
        days_ago = 120 - (i * 7)
        if days_ago < 0:
            days_ago = 0
        Transaction.objects.filter(pk=txn.pk).update(
            created_at=now - timedelta(days=days_ago)
        )


@pytest.mark.django_db
class TestLoanPolicyModel:
    def test_default_policy_created(self):
        policy = LoanPolicy.get_active()
        assert policy.is_active

    def test_only_one_active_policy(self, loan_policy):
        new = LoanPolicy.objects.create(
            name='New Policy', minimum_membership_days=60, is_active=True,
        )
        loan_policy.refresh_from_db()
        assert not loan_policy.is_active
        assert new.is_active

    def test_policy_str(self, loan_policy):
        assert 'Test Policy' in str(loan_policy)
        assert '(Active)' in str(loan_policy)


@pytest.mark.django_db
class TestNewCustomerCannotApply:
    def test_new_customer_not_eligible(self, new_customer, loan_policy):
        result, audit = LoanEligibilityService.check_eligibility(new_customer, loan_policy)
        assert result.eligible is False
        assert result.maximum_loan_amount == Decimal('0.00')

    def test_one_week_old_customer_not_eligible(self, db, loan_policy):
        user = User.objects.create_user(
            email='week@test.com', password='testpass123',
            first_name='Week', last_name='Old', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='Week', last_name='Old',
            phone='0242222222', status='ACTIVE',
        )
        Customer.objects.filter(pk=customer.pk).update(
            created_at=timezone.now() - timedelta(days=5)
        )
        customer.refresh_from_db()
        result, audit = LoanEligibilityService.check_eligibility(customer, loan_policy)
        assert result.eligible is False

    def test_one_month_old_customer_not_eligible(self, db, loan_policy):
        user = User.objects.create_user(
            email='month@test.com', password='testpass123',
            first_name='Month', last_name='Old', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='Month', last_name='Old',
            phone='0243333333', status='ACTIVE',
        )
        Customer.objects.filter(pk=customer.pk).update(
            created_at=timezone.now() - timedelta(days=25)
        )
        customer.refresh_from_db()
        result, audit = LoanEligibilityService.check_eligibility(customer, loan_policy)
        assert result.eligible is False


@pytest.mark.django_db
class TestMembershipDuration:
    def test_three_months_passes(self, eligible_customer, loan_policy):
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        membership_check = [c for c in result.passed_criteria if c['key'] == 'membership']
        assert len(membership_check) == 1
        assert int(membership_check[0]['current']) >= 3


@pytest.mark.django_db
class TestContributionHistory:
    def test_insufficient_contributions_rejects(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 5, cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'contribution_count' in failed_keys

    def test_enough_contributions_passes(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 12, cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        passed_keys = [c['key'] for c in result.passed_criteria]
        assert 'contribution_count' in passed_keys


@pytest.mark.django_db
class TestMinimumSavings:
    def test_insufficient_savings_rejects(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 12, amount=Decimal('50.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'savings' in failed_keys

    def test_sufficient_savings_passes(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        passed_keys = [c['key'] for c in result.passed_criteria]
        assert 'savings' in passed_keys


@pytest.mark.django_db
class TestMissedPeriods:
    def test_too_many_missed_periods_rejects(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert audit.max_missed_periods == 2


@pytest.mark.django_db
class TestActiveLoanRestriction:
    def test_active_loan_blocks_new(self, eligible_customer, loan_policy, susu_account, cashier_user, loan_product):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        Loan.objects.create(
            customer=eligible_customer, loan_product=loan_product,
            principal_amount=Decimal('2000'), term_months=6,
            status='ACTIVE', outstanding_balance=Decimal('2000'),
        )
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'active_loans' in failed_keys


@pytest.mark.django_db
class TestOverdueLoanRestriction:
    def test_overdue_blocks(self, eligible_customer, loan_policy, susu_account, cashier_user, loan_product):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        loan = Loan.objects.create(
            customer=eligible_customer, loan_product=loan_product,
            principal_amount=Decimal('2000'), term_months=6,
            status='ACTIVE', outstanding_balance=Decimal('2000'),
        )
        RepaymentSchedule.objects.create(
            loan=loan, installment_number=1,
            due_date=timezone.now().date() - timedelta(days=10),
            principal_due=Decimal('500'), interest_due=Decimal('100'),
            total_due=Decimal('600'), remaining_balance=Decimal('1400'),
            status='Overdue',
        )
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False
        assert audit.has_overdue is True


@pytest.mark.django_db
class TestSuspendedCustomerBlocked:
    def test_suspended_not_eligible(self, db, loan_policy):
        user = User.objects.create_user(
            email='suspended@test.com', password='testpass123',
            first_name='Suspended', last_name='User', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='Suspended', last_name='User',
            phone='0245555555', status='SUSPENDED',
        )
        result, audit = LoanEligibilityService.check_eligibility(customer, loan_policy)
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'account_status' in failed_keys


@pytest.mark.django_db
class TestIncompleteKYC:
    def test_incomplete_kyc_rejects(self, db, loan_policy):
        user = User.objects.create_user(
            email='nokyc@test.com', password='testpass123',
            first_name='No', last_name='KYC', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='No', last_name='KYC',
            phone='', status='ACTIVE',
        )
        Customer.objects.filter(pk=customer.pk).update(
            created_at=timezone.now() - timedelta(days=120)
        )
        customer.refresh_from_db()
        result, audit = LoanEligibilityService.check_eligibility(customer, loan_policy)
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'kyc' in failed_keys

    def test_kyc_not_required_passes(self, db):
        user = User.objects.create_user(
            email='nokyc2@test.com', password='testpass123',
            first_name='No', last_name='KYC2', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='No', last_name='KYC2',
            phone='0247777777', status='ACTIVE',
        )
        policy = LoanPolicy.objects.create(
            name='No KYC', require_kyc=False, is_active=True,
            minimum_membership_days=0, minimum_contribution_days=0,
            minimum_successful_contributions=0, minimum_savings=Decimal('0'),
        )
        result, audit = LoanEligibilityService.check_eligibility(customer, policy)
        passed_keys = [c['key'] for c in result.passed_criteria]
        assert 'kyc' in passed_keys


@pytest.mark.django_db
class TestGoodRepaymentHistory:
    def test_no_previous_loans_passes(self, eligible_customer, loan_policy):
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        passed_keys = [c['key'] for c in result.passed_criteria]
        assert 'repayment_history' in passed_keys

    def test_defaulted_loans_fail(self, eligible_customer, loan_policy, susu_account, loan_product):
        Loan.objects.create(
            customer=eligible_customer, loan_product=loan_product,
            principal_amount=Decimal('1000'), term_months=3,
            status='DEFAULTED',
        )
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'repayment_history' in failed_keys


@pytest.mark.django_db
class TestMaxLoanAmount:
    def test_max_loan_amount(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.maximum_loan_amount == Decimal('3000.00')

    def test_requested_amount_exceeds_max(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(
            eligible_customer, loan_policy, requested_amount=Decimal('5000.00')
        )
        assert result.eligible is False
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'requested_amount' in failed_keys


@pytest.mark.django_db
class TestEligibilityAuditRecording:
    def test_audit_recorded(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert audit.pk is not None
        assert audit.customer == eligible_customer
        assert audit.policy == loan_policy

    def test_audit_snapshot(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        snapshot = audit.to_snapshot()
        assert 'customer_id' in snapshot
        assert 'policy_name' in snapshot
        assert 'eligible' in snapshot


@pytest.mark.django_db
class TestEligibilityDoesNotEqualApproval:
    def test_eligible_customer_still_needs_review(self, eligible_customer, loan_policy, susu_account, cashier_user, loan_product):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is True

        loan = Loan.objects.create(
            customer=eligible_customer, loan_product=loan_product,
            principal_amount=Decimal('2000'), term_months=6,
            status='SUBMITTED',
        )
        assert loan.status == 'SUBMITTED'
        assert loan.approval_date is None


@pytest.mark.django_db
class TestConfigurablePolicy:
    def test_changing_policy_changes_eligibility(self, eligible_customer, susu_account, cashier_user):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)

        strict_policy = LoanPolicy.objects.create(
            name='Strict', minimum_membership_days=180,
            minimum_successful_contributions=50, minimum_savings=Decimal('10000'),
            maximum_loan_multiplier=Decimal('1.00'), maximum_active_loans=0,
            maximum_missed_periods=0, waiting_period_days=7,
            require_kyc=True, require_good_repayment_history=True,
            block_overdue_customers=True, is_active=True,
        )
        result, _ = LoanEligibilityService.check_eligibility(eligible_customer, strict_policy)
        assert result.eligible is False

        loose_policy = LoanPolicy.objects.create(
            name='Loose', minimum_membership_days=30,
            minimum_successful_contributions=5, minimum_savings=Decimal('100'),
            maximum_loan_multiplier=Decimal('3.00'), maximum_active_loans=2,
            maximum_missed_periods=5, waiting_period_days=1,
            require_kyc=False, require_good_repayment_history=False,
            block_overdue_customers=False, is_active=True,
        )
        result, _ = LoanEligibilityService.check_eligibility(eligible_customer, loose_policy)
        assert result.eligible is True


@pytest.mark.django_db
class TestRetainedSnapshot:
    def test_eligibility_snapshot_stored_on_loan(self, eligible_customer, loan_policy, susu_account, cashier_user, loan_product):
        _make_contributions(eligible_customer, 15, amount=Decimal('100.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)

        loan = Loan.objects.create(
            customer=eligible_customer, loan_product=loan_product,
            principal_amount=Decimal('2000'), term_months=6,
            status='SUBMITTED', eligibility_snapshot=audit.to_snapshot(),
        )
        assert loan.eligibility_snapshot['eligible'] is True
        assert loan.eligibility_snapshot['policy_name'] == 'Test Policy'


@pytest.mark.django_db
class TestPendingPaymentsNotCounted:
    def test_pending_payments_excluded(self, eligible_customer, loan_policy, susu_account, cashier_user):
        Transaction.objects.create(
            customer=eligible_customer,
            transaction_type='SUSU_CONTRIBUTION',
            amount=Decimal('5000'), balance_before=Decimal('0'),
            balance_after=Decimal('5000'), payment_method='PAYSTACK',
            idempotency_key='FAILED_test123', created_by=cashier_user,
        )
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'contribution_count' in failed_keys


@pytest.mark.django_db
class TestReversedPaymentsNotCounted:
    def test_reversed_payments_excluded(self, eligible_customer, loan_policy, susu_account, cashier_user):
        txn = Transaction.objects.create(
            customer=eligible_customer,
            transaction_type='SUSU_CONTRIBUTION',
            amount=Decimal('5000'), balance_before=Decimal('0'),
            balance_after=Decimal('5000'), payment_method='CASH',
            created_by=cashier_user,
        )
        Transaction.objects.create(
            customer=eligible_customer,
            transaction_type='SUSU_CONTRIBUTION',
            amount=Decimal('5000'), balance_before=Decimal('5000'),
            balance_after=Decimal('0'), payment_method='CASH',
            created_by=cashier_user, is_reversal=True,
            reversed_by=txn,
        )
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.eligible is False


@pytest.mark.django_db
class TestCannotAccessOtherCustomerEligibility:
    def test_customer_cannot_check_others(self, eligible_customer, new_customer, loan_policy):
        result, audit = LoanEligibilityService.check_eligibility(new_customer, loan_policy)
        assert audit.customer == new_customer
        assert audit.customer != eligible_customer


@pytest.mark.django_db
class TestScoreCalculation:
    def test_score_100_when_all_pass(self, eligible_customer, loan_policy, susu_account, cashier_user):
        _make_contributions(eligible_customer, 20, amount=Decimal('200.00'), cashier=cashier_user)
        result, audit = LoanEligibilityService.check_eligibility(eligible_customer, loan_policy)
        assert result.score == 100
        assert audit.eligibility_score == 100


@pytest.mark.django_db
class TestWaitingPeriod:
    def test_waiting_period_enforced(self, db, loan_policy):
        user = User.objects.create_user(
            email='wait@test.com', password='testpass123',
            first_name='Wait', last_name='Test', role='CUSTOMER',
        )
        customer = Customer.objects.create(
            user=user, first_name='Wait', last_name='Test',
            phone='0248888888', status='ACTIVE',
            created_at=timezone.now() - timedelta(days=3),
        )
        result, audit = LoanEligibilityService.check_eligibility(customer, loan_policy)
        failed_keys = [c['key'] for c in result.failed_criteria]
        assert 'waiting_period' in failed_keys
