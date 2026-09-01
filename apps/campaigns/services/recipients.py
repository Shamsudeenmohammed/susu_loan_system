"""
Recipient resolution for SMS campaigns.

Recipients are ALWAYS resolved dynamically from the database at the time a
campaign is prepared/sent. No permanent list of recipients is stored — the
campaign stores the target group + filter configuration, and the query runs
against the live customer/loan/repayment/contribution data.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.campaigns.models import SMSCampaign


# Frequency -> approximate days between contributions (used only because Zemzem
# has no per-account contribution schedule model; transactions are the ledger).
FREQUENCY_DAYS = {
    SusuAccount.Frequency.DAILY: 1,
    SusuAccount.Frequency.WEEKLY: 7,
    SusuAccount.Frequency.BIWEEKLY: 14,
    SusuAccount.Frequency.MONTHLY: 30,
    SusuAccount.Frequency.CUSTOM: 30,
}

ACTIVE_STATUS = Customer.Status.ACTIVE


def _base_active_customers():
    """All customers whose account is active (eligible to receive SMS)."""
    return Customer.objects.filter(status=ACTIVE_STATUS)


def _loan_statuses():
    return ['ACTIVE', 'DISBURSED']


def customers_with_active_loans():
    return _base_active_customers().filter(
        loans__status__in=_loan_statuses()
    ).distinct()


def customers_with_outstanding_repayments():
    return _base_active_customers().filter(
        loans__repayment_schedules__status__in=['PENDING', 'PARTIALLY_PAID'],
        loans__outstanding_balance__gt=0,
    ).distinct()


def customers_with_overdue_repayments(reference_date=None):
    today = reference_date or timezone.now().date()
    return _base_active_customers().filter(
        loans__repayment_schedules__due_date__lt=today,
        loans__repayment_schedules__status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue'],
    ).distinct()


def customers_with_susu_accounts():
    return _base_active_customers().filter(
        susu_accounts__status=SusuAccount.Status.ACTIVE
    ).distinct()


def _next_due_date(account, reference_date):
    """Heuristic next contribution due date based on frequency + opened date."""
    anchor = account.activated_at or account.opened_at
    if anchor is None:
        return None
    anchor_date = anchor.date()
    days = FREQUENCY_DAYS.get(account.contribution_frequency, 7)
    if days <= 0:
        return None
    # advance by full periods until at-or-past today
    periods = 0
    due = anchor_date
    while due < reference_date:
        periods += 1
        due = anchor_date + timedelta(days=days * periods)
    return due


def customers_with_due_contributions(reference_date=None, horizon_days=2):
    """Active Susu customers whose next contribution is due within the window."""
    today = reference_date or timezone.now().date()
    ids = set()
    accounts = SusuAccount.objects.filter(
        status=SusuAccount.Status.ACTIVE,
        customer__status=ACTIVE_STATUS,
    ).select_related('customer')
    for account in accounts:
        due = _next_due_date(account, today)
        if due is None:
            continue
        if today <= due <= today + timedelta(days=horizon_days):
            ids.add(account.customer_id)
    return Customer.objects.filter(pk__in=ids)


def customers_with_overdue_contributions(reference_date=None):
    """Active Susu customers whose most recent expected contribution is overdue."""
    today = reference_date or timezone.now().date()
    ids = set()
    accounts = SusuAccount.objects.filter(
        status=SusuAccount.Status.ACTIVE,
        customer__status=ACTIVE_STATUS,
    ).select_related('customer')
    for account in accounts:
        due = _next_due_date(account, today)
        if due is None:
            continue
        if due < today:
            ids.add(account.customer_id)
    return Customer.objects.filter(pk__in=ids)


def recently_approved_customers(days=30, reference_date=None):
    today = reference_date or timezone.now().date()
    since = timezone.now() - timedelta(days=days)
    return _base_active_customers().filter(approved_at__gte=since)


def recently_activated_customers(days=30):
    since = timezone.now() - timedelta(days=days)
    return _base_active_customers().filter(
        susu_accounts__status=SusuAccount.Status.ACTIVE,
        susu_accounts__activated_at__gte=since,
    ).distinct()


def manual_customers(customer_ids):
    return Customer.objects.filter(pk__in=customer_ids)


def apply_customer_filters(qs, filters):
    """Apply additional optional filters to a candidate queryset."""
    if not filters:
        return qs
    status = filters.get('status')
    if status:
        qs = qs.filter(status=status.upper())

    loan = filters.get('loan')
    if loan == 'ACTIVE_LOAN':
        qs = qs.filter(loans__status__in=_loan_statuses()).distinct()
    elif loan == 'OUTSTANDING_LOAN':
        qs = qs.filter(
            Q(loans__status__in=_loan_statuses()) & Q(loans__outstanding_balance__gt=0)
        ).distinct()
    elif loan == 'NO_ACTIVE_LOAN':
        qs = qs.exclude(loans__status__in=_loan_statuses()).distinct()

    repayment = filters.get('repayment')
    today = timezone.now().date()
    if repayment == 'DUE_TODAY':
        qs = qs.filter(
            loans__repayment_schedules__due_date=today,
            loans__repayment_schedules__status__in=['PENDING', 'PARTIALLY_PAID'],
        ).distinct()
    elif repayment == 'DUE_TOMORROW':
        qs = qs.filter(
            loans__repayment_schedules__due_date=today + timedelta(days=1),
            loans__repayment_schedules__status__in=['PENDING', 'PARTIALLY_PAID'],
        ).distinct()
    elif repayment == 'OVERDUE':
        qs = qs.filter(
            loans__repayment_schedules__due_date__lt=today,
            loans__repayment_schedules__status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue'],
        ).distinct()

    contribution = filters.get('contribution')
    if contribution == 'ACTIVE':
        qs = qs.filter(susu_accounts__status=SusuAccount.Status.ACTIVE).distinct()
    elif contribution == 'DUE':
        due_ids = customers_with_due_contributions().values_list('pk', flat=True)
        qs = qs.filter(pk__in=due_ids)
    elif contribution == 'OVERDUE':
        overdue_ids = customers_with_overdue_contributions().values_list('pk', flat=True)
        qs = qs.filter(pk__in=overdue_ids)

    return qs


def resolve_recipients(campaign, reference_date=None):
    """
    Resolve the candidate Customer queryset for a campaign given its target
    group + filters. Always restricts to active customers, then applies the
    extra filters. Returns a list of Customer instances.
    """
    group = campaign.target_group

    if group == SMSCampaign.TargetGroup.ALL_ACTIVE:
        qs = _base_active_customers()
    elif group == SMSCampaign.TargetGroup.ACTIVE_LOANS:
        qs = customers_with_active_loans()
    elif group == SMSCampaign.TargetGroup.OUTSTANDING_REPAYMENTS:
        qs = customers_with_outstanding_repayments()
    elif group == SMSCampaign.TargetGroup.OVERDUE_REPAYMENTS:
        qs = customers_with_overdue_repayments(reference_date)
    elif group == SMSCampaign.TargetGroup.SUSU_ACCOUNTS:
        qs = customers_with_susu_accounts()
    elif group == SMSCampaign.TargetGroup.DUE_CONTRIBUTIONS:
        qs = customers_with_due_contributions(reference_date)
    elif group == SMSCampaign.TargetGroup.OVERDUE_CONTRIBUTIONS:
        qs = customers_with_overdue_contributions(reference_date)
    elif group == SMSCampaign.TargetGroup.RECENTLY_APPROVED:
        qs = recently_approved_customers(reference_date=reference_date)
    elif group == SMSCampaign.TargetGroup.RECENTLY_ACTIVATED:
        qs = recently_activated_customers()
    elif group == SMSCampaign.TargetGroup.MANUAL_SELECTION:
        qs = manual_customers(campaign.manual_customer_ids or [])
        # Manual selection still respects customer filters; if no status filter
        # is set, restrict to active customers for safety.
        if not campaign.filters.get('status'):
            qs = qs.filter(status=ACTIVE_STATUS)
    else:
        qs = _base_active_customers()

    qs = apply_customer_filters(qs, campaign.filters or {})
    # Final safety: never send to non-active customers unless explicitly asked
    # to target inactive via the status filter.
    if campaign.filters.get('status') in ('INACTIVE', 'PENDING', 'SUSPENDED'):
        pass
    else:
        qs = qs.filter(status=ACTIVE_STATUS)

    return list(qs.select_related('user').distinct().order_by('pk'))
