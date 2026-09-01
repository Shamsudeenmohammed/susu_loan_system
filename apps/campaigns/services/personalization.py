"""
Message personalization for SMS campaigns.

Builds a per-customer context dictionary from real Zemzem data and substitutes
{{placeholders}} in the campaign message before sending. Only the placeholders
that make sense for the selected campaign type are exposed in the UI.
"""
import re
from decimal import Decimal

from django.utils import timezone

from apps.loans.models import RepaymentSchedule
from apps.susu.models import SusuAccount

from .recipients import _next_due_date

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


# Map of campaign types to the set of placeholders they support (drives UI hints).
CAMPAIGN_PLACEHOLDERS = {
    'GENERAL_ANNOUNCEMENT': {'customer_name', 'first_name'},
    'REPAYMENT_REMINDER': {'customer_name', 'first_name', 'account_number',
                           'repayment_amount', 'due_date', 'outstanding_balance'},
    'OVERDUE_REPAYMENT_REMINDER': {'customer_name', 'first_name', 'account_number',
                                   'repayment_amount', 'due_date', 'outstanding_balance'},
    'CONTRIBUTION_REMINDER': {'customer_name', 'first_name', 'account_number',
                              'contribution_amount', 'contribution_due_date'},
    'LOAN_NOTIFICATION': {'customer_name', 'first_name', 'account_number',
                          'outstanding_balance'},
    'ACCOUNT_APPROVAL': {'customer_name', 'first_name'},
    'ACCOUNT_ACTIVATION': {'customer_name', 'first_name'},
    'SUSU_ACTIVATION': {'customer_name', 'first_name', 'account_number'},
    'PAYMENT_CONFIRMATION': {'customer_name', 'first_name', 'account_number'},
    'CUSTOM_MESSAGE': {'customer_name', 'first_name'},
}

ALL_PLACEHOLDERS = {
    'customer_name': 'Customer full name',
    'first_name': "Customer's first name",
    'account_number': 'Susu account / loan number',
    'repayment_amount': 'Amount due for next repayment',
    'due_date': 'Next repayment due date',
    'outstanding_balance': "Loan's outstanding balance",
    'contribution_amount': 'Expected Susu contribution amount',
    'contribution_due_date': 'Next contribution due date',
}


def _money(value):
    if value is None:
        return '0.00'
    return f"{Decimal(value):,.2f}"


def _next_repayment_context(customer):
    """Resolve the customer's next relevant repayment schedule + loan numbers."""
    loan = customer.loans.filter(
        status__in=['ACTIVE', 'DISBURSED']
    ).order_by('-created_at').first()
    if loan is None:
        return {}
    schedule = RepaymentSchedule.objects.filter(
        loan=loan,
        status__in=['PENDING', 'PARTIALLY_PAID'],
    ).order_by('due_date').first()
    account_number = loan.loan_number
    amount_due = schedule.total_due - schedule.amount_paid if schedule else None
    due_date = schedule.due_date if schedule else None
    return {
        'account_number': account_number,
        'repayment_amount': _money(amount_due) if amount_due is not None else _money(loan.outstanding_balance),
        'due_date': due_date.strftime('%d %B') if due_date else '',
        'outstanding_balance': _money(loan.outstanding_balance),
    }


def _next_contribution_context(customer, reference_date=None):
    """Resolve the customer's active Susu account + contribution data."""
    account = SusuAccount.objects.filter(
        customer=customer,
        status=SusuAccount.Status.ACTIVE,
    ).order_by('-activated_at', '-opened_at').first()
    if account is None:
        return {}
    today = reference_date or timezone.now().date()
    due = _next_due_date(account, today)
    return {
        'account_number': account.account_number,
        'contribution_amount': _money(account.expected_contribution),
        'contribution_due_date': due.strftime('%d %B') if due else '',
    }


def build_context(customer, campaign_type, reference_date=None):
    """Build the personalization context dict for a customer + campaign type."""
    ctx = {
        'customer_name': customer.get_full_name(),
        'first_name': customer.first_name,
    }
    if campaign_type in (
        'REPAYMENT_REMINDER', 'OVERDUE_REPAYMENT_REMINDER', 'LOAN_NOTIFICATION',
        'PAYMENT_CONFIRMATION',
    ):
        ctx.update(_next_repayment_context(customer))
    if campaign_type in (
        'CONTRIBUTION_REMINDER', 'SUSU_ACTIVATION', 'PAYMENT_CONFIRMATION',
    ):
        ctx.update(_next_contribution_context(customer, reference_date))
    if campaign_type in ('ACCOUNT_ACTIVATION', 'ACCOUNT_APPROVAL'):
        ctx['account_number'] = customer.customer_number
    return ctx


def personalize(message, context):
    """Replace {{token}} placeholders in a message using the context dict."""
    def repl(match):
        key = match.group(1)
        return str(context.get(key, match.group(0)))
    return TOKEN_RE.sub(repl, message)
