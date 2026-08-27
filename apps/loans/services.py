from decimal import Decimal
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


def calculate_repayment_schedule(loan):
    """
    Generate repayment schedule for a loan.
    Returns list of installment dicts.
    """
    from apps.loans.models import LoanProduct

    product = loan.loan_product
    principal = loan.principal_amount
    term = loan.term_months
    total_interest = product.calculate_total_interest(principal, term)

    if product.interest_method == LoanProduct.InterestMethod.FLAT:
        return _flat_schedule(loan, principal, total_interest, term)
    else:
        return _reducing_balance_schedule(loan, principal, total_interest, term)


def _flat_schedule(loan, principal, total_interest, term):
    """Generate flat interest schedule."""
    monthly_principal = (principal / Decimal(str(term))).quantize(Decimal('0.01'))
    monthly_interest = (total_interest / Decimal(str(term))).quantize(Decimal('0.01'))
    total_per_month = monthly_principal + monthly_interest

    schedule = []
    balance = principal + total_interest

    frequency_days = _get_frequency_days(loan.repayment_frequency)
    num_installments = _get_num_installments(term, loan.repayment_frequency)
    current_date = loan.disbursement_date.date() if loan.disbursement_date else date.today()

    for i in range(1, num_installments + 1):
        current_date = current_date + timedelta(days=frequency_days)
        interest_share = monthly_interest if i < num_installments else (total_interest - monthly_interest * (num_installments - 1))
        principal_share = monthly_principal if i < num_installments else balance - monthly_interest * (num_installments - 1) - (balance - principal - total_interest)
        total_due = principal_share + interest_share

        balance = balance - total_due
        if balance < 0:
            balance = Decimal('0.00')

        schedule.append({
            'installment_number': i,
            'due_date': current_date,
            'principal_due': principal_share.quantize(Decimal('0.01')),
            'interest_due': interest_share.quantize(Decimal('0.01')),
            'total_due': total_due.quantize(Decimal('0.01')),
            'remaining_balance': max(balance, Decimal('0.00')),
        })

    return schedule


def _reducing_balance_schedule(loan, principal, total_interest, term):
    """Generate reducing balance schedule."""
    monthly_rate = (loan.loan_product.interest_rate / Decimal('100')) / Decimal('12')
    monthly_principal = (principal / Decimal(str(term))).quantize(Decimal('0.01'))

    schedule = []
    balance = principal

    frequency_days = _get_frequency_days(loan.repayment_frequency)
    num_installments = _get_num_installments(term, loan.repayment_frequency)
    current_date = loan.disbursement_date.date() if loan.disbursement_date else date.today()

    for i in range(1, num_installments + 1):
        current_date = current_date + timedelta(days=frequency_days)
        interest = (balance * monthly_rate).quantize(Decimal('0.01'))
        total_due = monthly_principal + interest
        balance = balance - monthly_principal

        schedule.append({
            'installment_number': i,
            'due_date': current_date,
            'principal_due': monthly_principal,
            'interest_due': interest,
            'total_due': total_due.quantize(Decimal('0.01')),
            'remaining_balance': max(balance, Decimal('0.00')),
        })

    return schedule


def _get_frequency_days(frequency):
    mapping = {
        'DAILY': 1,
        'WEEKLY': 7,
        'BIWEEKLY': 14,
        'MONTHLY': 30,
    }
    return mapping.get(frequency, 30)


def _get_num_installments(term_months, frequency):
    mapping = {
        'DAILY': term_months * 30,
        'WEEKLY': term_months * 4,
        'BIWEEKLY': term_months * 2,
        'MONTHLY': term_months,
    }
    return mapping.get(frequency, term_months)


def apply_repayment(loan, installment, amount):
    """
    Apply a repayment amount to a specific installment.
    Returns (updated_installment, remaining_amount).
    """
    remaining = installment.amount_remaining

    if amount >= remaining:
        excess = amount - remaining
        installment.amount_paid = installment.total_due
        installment.status = 'PAID'
        installment.paid_date = date.today()
        installment.save()
        return installment, excess
    else:
        installment.amount_paid += amount
        installment.status = 'PARTIALLY_PAID'
        installment.save()
        return installment, Decimal('0.00')
