from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db.models import Q, Sum, Count


class EligibilityResult:
    def __init__(self):
        self.eligible = False
        self.maximum_loan_amount = Decimal('0.00')
        self.score = 0
        self.passed_criteria = []
        self.failed_criteria = []
        self.reasons = []
        self.details = {}
        self.membership_months = 0
        self.required_membership_months = 0
        self.successful_contributions = 0
        self.required_contributions = 0
        self.total_savings = Decimal('0.00')
        self.minimum_savings = Decimal('0.00')
        self.missed_periods = 0
        self.max_missed_periods = 0
        self.active_loans = 0
        self.has_overdue = False
        self.is_kyc_complete = False
        self.contribution_period_months = 0
        self.required_contribution_months = 0

    def to_dict(self):
        return {
            'eligible': self.eligible,
            'status': 'ELIGIBLE' if self.eligible else 'NOT ELIGIBLE',
            'maximum_loan_amount': str(self.maximum_loan_amount),
            'eligibility_score': self.score,
            'passed_criteria': self.passed_criteria,
            'failed_criteria': self.failed_criteria,
            'reasons': self.reasons,
            'details': self.details,
        }

    def _add_pass(self, key, label, current, required, detail_str=None):
        self.passed_criteria.append({
            'key': key,
            'label': label,
            'current': str(current),
            'required': str(required),
            'detail': detail_str or f'{current} / {required}',
        })

    def _add_fail(self, key, label, current, required, detail_str=None, reason=''):
        self.failed_criteria.append({
            'key': key,
            'label': label,
            'current': str(current),
            'required': str(required),
            'detail': detail_str or f'{current} / {required}',
            'reason': reason,
        })
        if reason:
            self.reasons.append(reason)


class LoanEligibilityService:
    ACTIVE_LOAN_STATUSES = ['APPROVED', 'DISBURSED', 'ACTIVE', 'PARTIALLY_PAID', 'OVERDUE']

    @classmethod
    def check_eligibility(cls, customer, policy=None, requested_amount=None):
        from apps.loans.models import LoanPolicy, Loan, EligibilityAudit

        if policy is None:
            policy = LoanPolicy.get_active()

        result = EligibilityResult()
        now = timezone.now()

        cls._check_account_status(customer, result, policy)
        cls._check_membership_duration(customer, result, policy, now)
        cls._check_kyc(customer, result, policy)
        cls._check_contribution_history(customer, result, policy, now)
        cls._check_savings(customer, result, policy)
        cls._check_contribution_consistency(customer, result, policy, now)
        cls._check_active_loans(customer, result, policy)
        cls._check_overdue_loans(customer, result, policy)
        cls._check_repayment_history(customer, result, policy)
        cls._check_waiting_period(customer, result, policy, now)
        cls._calculate_max_loan(customer, result, policy)

        if requested_amount and result.maximum_loan_amount > 0:
            if requested_amount > result.maximum_loan_amount:
                result._add_fail(
                    'requested_amount', 'Requested Amount',
                    f'GHS {requested_amount:.2f}', f'GHS {result.maximum_loan_amount:.2f}',
                    reason=f'Requested amount exceeds your maximum eligible loan amount of GHS {result.maximum_loan_amount:.2f}.'
                )

        total_criteria = len(result.passed_criteria) + len(result.failed_criteria)
        if total_criteria > 0:
            result.score = int((len(result.passed_criteria) / total_criteria) * 100)

        result.eligible = len(result.failed_criteria) == 0

        audit = EligibilityAudit.objects.create(
            customer=customer,
            policy=policy,
            eligible=result.eligible,
            maximum_loan_amount=result.maximum_loan_amount,
            eligibility_score=result.score,
            passed_criteria=[c['key'] for c in result.passed_criteria],
            failed_criteria=[c['key'] for c in result.failed_criteria],
            membership_months=result.membership_months,
            required_membership_months=result.required_membership_months,
            successful_contributions=result.successful_contributions,
            required_contributions=result.required_contributions,
            total_savings=result.total_savings,
            minimum_savings=result.minimum_savings,
            missed_periods=result.missed_periods,
            max_missed_periods=result.max_missed_periods,
            active_loans=result.active_loans,
            has_overdue=result.has_overdue,
            is_kyc_complete=result.is_kyc_complete,
            contribution_period_months=result.contribution_period_months,
            required_contribution_months=result.required_contribution_months,
            snapshot_json=result.to_dict(),
        )

        return result, audit

    @classmethod
    def _check_account_status(cls, customer, result, policy):
        is_active = customer.status == 'ACTIVE'
        if is_active:
            result._add_pass('account_status', 'Account Status', customer.status, 'ACTIVE')
        else:
            result._add_fail(
                'account_status', 'Account Status', customer.status, 'ACTIVE',
                reason=f'Your account status is {customer.status}. Only ACTIVE accounts can apply for loans.'
            )

    @classmethod
    def _check_membership_duration(cls, customer, result, policy, now):
        registration_date = customer.created_at
        membership_days = (now - registration_date).days
        membership_months = membership_days // 30
        required_months = policy.minimum_membership_days // 30
        result.membership_months = membership_months
        result.required_membership_months = required_months
        passed = membership_months >= required_months

        label = 'Membership Duration'
        if passed:
            result._add_pass('membership', label, membership_months, required_months,
                             f'{membership_months} months (required: {required_months})')
        else:
            result._add_fail(
                'membership', label, membership_months, required_months,
                reason=f'Membership period: {membership_months} months. Required: {required_months} months. Please maintain your account for another {required_months - membership_months} month(s).'
            )

    @classmethod
    def _check_kyc(cls, customer, result, policy):
        if not policy.require_kyc:
            result._add_pass('kyc', 'KYC / Profile', 'Not Required', 'Not Required')
            return

        required_fields = ['first_name', 'last_name', 'phone']
        optional_fields = ['address', 'id_type', 'id_number', 'emergency_contact_name', 'emergency_contact_phone']
        missing = [f for f in required_fields if not getattr(customer, f, None)]

        is_complete = len(missing) == 0
        result.is_kyc_complete = is_complete
        if is_complete:
            result._add_pass('kyc', 'KYC / Profile', 'Complete', 'Complete')
        else:
            result._add_fail(
                'kyc', 'KYC / Profile', 'Incomplete', 'Complete',
                reason=f'Please complete your profile. Missing: {", ".join(missing)}.'
            )

    @classmethod
    def _check_contribution_history(cls, customer, result, policy, now):
        from apps.payments.models import Transaction

        successful_txns = Transaction.objects.filter(
            customer=customer,
            transaction_type='SUSU_CONTRIBUTION',
            is_reversal=False,
        ).exclude(
            idempotency_key__startswith='FAILED_'
        )

        earliest = successful_txns.order_by('created_at').values_list('created_at', flat=True).first()
        if earliest:
            contribution_days = (now - earliest).days
            contribution_months = contribution_days // 30
        else:
            contribution_months = 0

        count = successful_txns.count()
        required_count = policy.minimum_successful_contributions
        required_months = policy.minimum_contribution_days // 30
        result.successful_contributions = count
        result.required_contributions = required_count
        result.contribution_period_months = contribution_months
        result.required_contribution_months = required_months

        passed_count = count >= required_count
        passed_period = contribution_months >= required_months

        if passed_period:
            result._add_pass('contribution_period', 'Contribution Period', contribution_months, required_months)
        else:
            result._add_fail(
                'contribution_period', 'Contribution Period', contribution_months, required_months,
                reason=f'Active contribution period: {contribution_months} month(s). Required: {required_months} month(s).'
            )

        if passed_count:
            result._add_pass('contribution_count', 'Successful Contributions', count, required_count)
        else:
            result._add_fail(
                'contribution_count', 'Successful Contributions', count, required_count,
                reason=f'You have {count} successful contribution(s). Required: {required_count}. Make {required_count - count} more contribution(s).'
            )

    @classmethod
    def _check_savings(cls, customer, result, policy):
        from apps.payments.models import Transaction

        total = Transaction.objects.filter(
            customer=customer,
            transaction_type='SUSU_CONTRIBUTION',
            is_reversal=False,
        ).exclude(
            idempotency_key__startswith='FAILED_'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        withdrawals = Transaction.objects.filter(
            customer=customer,
            transaction_type='WITHDRAWAL',
            is_reversal=False,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        savings = total - withdrawals
        minimum = policy.minimum_savings
        result.total_savings = savings
        result.minimum_savings = minimum
        passed = savings >= minimum

        if passed:
            result._add_pass('savings', 'Savings Balance', str(savings), str(minimum),
                             f'GHS {savings:.2f} (minimum: GHS {minimum:.2f})')
        else:
            result._add_fail(
                'savings', 'Savings Balance', str(savings), str(minimum),
                reason=f'Savings: GHS {savings:.2f}. Required: GHS {minimum:.2f}. You need GHS {minimum - savings:.2f} more in savings.'
            )

    @classmethod
    def _check_contribution_consistency(cls, customer, result, policy, now):
        from apps.payments.models import Transaction
        from apps.susu.models import SusuAccount

        account = SusuAccount.objects.filter(customer=customer, status='ACTIVE').first()
        if not account:
            result._add_pass('consistency', 'Contribution Consistency', 'N/A', 'N/A')
            return

        frequency = account.contribution_frequency
        freq_days = {'DAILY': 1, 'WEEKLY': 7, 'BIWEEKLY': 14, 'MONTHLY': 30}.get(frequency, 30)

        txns = Transaction.objects.filter(
            customer=customer,
            transaction_type='SUSU_CONTRIBUTION',
            is_reversal=False,
        ).order_by('created_at')

        if not txns.exists():
            result._add_pass('consistency', 'Contribution Consistency', '0 missed', str(policy.maximum_missed_periods))
            return

        first_txn = txns.first().created_at
        total_weeks = max((now - first_txn).days // freq_days, 1)

        completed_periods = txns.values('created_at__date').distinct().count()
        missed = max(0, total_weeks - completed_periods)
        max_missed = policy.maximum_missed_periods
        result.missed_periods = missed
        result.max_missed_periods = max_missed
        passed = missed <= max_missed

        if passed:
            result._add_pass('consistency', 'Contribution Consistency', f'{missed} missed', str(max_missed),
                             f'{missed} missed period(s) (maximum allowed: {max_missed})')
        else:
            result._add_fail(
                'consistency', 'Contribution Consistency', f'{missed} missed', str(max_missed),
                reason=f'You have {missed} missed contribution period(s). Maximum allowed: {max_missed}.'
            )

    @classmethod
    def _check_active_loans(cls, customer, result, policy):
        from apps.loans.models import Loan

        active = Loan.objects.filter(
            customer=customer,
            status__in=cls.ACTIVE_LOAN_STATUSES,
        ).count()

        max_active = policy.maximum_active_loans
        result.active_loans = active
        passed = active < max_active

        if passed:
            result._add_pass('active_loans', 'Active Loans', str(active), str(max_active),
                             f'{active} active loan(s) (maximum: {max_active})')
        else:
            result._add_fail(
                'active_loans', 'Active Loans', str(active), str(max_active),
                reason=f'You already have {active} active loan(s). Maximum allowed: {max_active}. Please repay your existing loan before applying for another.'
            )

    @classmethod
    def _check_overdue_loans(cls, customer, result, policy):
        if not policy.block_overdue_customers:
            result._add_pass('overdue', 'Overdue Loans', 'Not Blocked', 'Not Blocked')
            return

        from apps.loans.models import RepaymentSchedule

        has_overdue = RepaymentSchedule.objects.filter(
            loan__customer=customer,
            status__in=['Overdue', 'PENDING', 'PARTIALLY_PAID'],
            due_date__lt=timezone.now().date(),
        ).exists()

        result.has_overdue = has_overdue
        if has_overdue:
            result._add_fail(
                'overdue', 'Overdue Loans', 'Yes', 'No',
                reason='You currently have overdue loan repayment(s). Please clear your overdue balance before applying for another loan.'
            )
        else:
            result._add_pass('overdue', 'Overdue Loans', 'None', 'None')

    @classmethod
    def _check_repayment_history(cls, customer, result, policy):
        if not policy.require_good_repayment_history:
            result._add_pass('repayment_history', 'Repayment History', 'Not Required', 'Not Required')
            return

        from apps.loans.models import Loan

        previous_loans = Loan.objects.filter(customer=customer, status__in=['COMPLETED', 'DEFAULTED'])
        total = previous_loans.count()

        if total == 0:
            result._add_pass('repayment_history', 'Repayment History', 'No previous loans', 'Good',
                             'No previous loans (considered good)')
            return

        defaulted = previous_loans.filter(status='DEFAULTED').count()
        repaid = previous_loans.filter(status='COMPLETED').count()
        completion_rate = (repaid / total) * 100 if total > 0 else 0
        is_good = defaulted == 0 or completion_rate >= 80

        if is_good:
            result._add_pass('repayment_history', 'Repayment History', f'{completion_rate:.0f}% completion', 'Good',
                             f'{repaid}/{total} loans fully repaid ({completion_rate:.0f}%)')
        else:
            result._add_fail(
                'repayment_history', 'Repayment History', f'{defaulted} defaulted', 'Good',
                reason=f'Poor repayment history: {defaulted} defaulted loan(s) out of {total}.'
            )

    @classmethod
    def _check_waiting_period(cls, customer, result, policy, now):
        days_since = (now - customer.created_at).days
        required = policy.waiting_period_days
        passed = days_since >= required

        if passed:
            result._add_pass('waiting_period', 'Waiting Period', f'{days_since} days', f'{required} days')
        else:
            result._add_fail(
                'waiting_period', 'Waiting Period', f'{days_since} days', f'{required} days',
                reason=f'Please wait {required - days_since} more day(s) before applying for a loan.'
            )

    @classmethod
    def _calculate_max_loan(cls, customer, result, policy):
        from apps.payments.models import Transaction

        total_contributions = Transaction.objects.filter(
            customer=customer,
            transaction_type='SUSU_CONTRIBUTION',
            is_reversal=False,
        ).exclude(
            idempotency_key__startswith='FAILED_'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        total_withdrawals = Transaction.objects.filter(
            customer=customer,
            transaction_type='WITHDRAWAL',
            is_reversal=False,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        savings = total_contributions - total_withdrawals
        max_loan = (savings * policy.maximum_loan_multiplier).quantize(Decimal('0.01'))
        result.maximum_loan_amount = max(Decimal('0.00'), max_loan)
