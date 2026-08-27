from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.payments.models import Transaction, Withdrawal
from apps.loans.models import Loan, LoanRepayment, RepaymentSchedule


@login_required
def dashboard_view(request):
    if request.user.has_role('CUSTOMER'):
        return _customer_dashboard(request)
    return _staff_dashboard(request)


def _staff_dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)

    total_customers = Customer.objects.filter(status='ACTIVE').count()
    active_susu = SusuAccount.objects.filter(status='ACTIVE').count()

    total_contributions = Transaction.objects.filter(
        transaction_type='SUSU_CONTRIBUTION'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    today_contributions = Transaction.objects.filter(
        transaction_type='SUSU_CONTRIBUTION',
        created_at__date=today
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    total_withdrawals = Transaction.objects.filter(
        transaction_type='WITHDRAWAL'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    outstanding_loans = Loan.objects.filter(
        status__in=['ACTIVE', 'DISBURSED']
    ).aggregate(total=Sum('outstanding_balance'))['total'] or Decimal('0.00')

    total_disbursed = Transaction.objects.filter(
        transaction_type='LOAN_DISBURSEMENT'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    total_repayments = Transaction.objects.filter(
        transaction_type='LOAN_REPAYMENT'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    overdue_count = RepaymentSchedule.objects.filter(
        due_date__lt=today,
        status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue']
    ).count()

    pending_applications = Loan.objects.filter(status='SUBMITTED').count()

    # Chart data - contributions last 30 days
    contributions_chart = []
    for i in range(30):
        date = today - timedelta(days=29 - i)
        amount = Transaction.objects.filter(
            transaction_type='SUSU_CONTRIBUTION',
            created_at__date=date
        ).aggregate(total=Sum('amount'))['total'] or 0
        contributions_chart.append({'date': date.strftime('%b %d'), 'amount': float(amount)})

    # Recent transactions
    recent_transactions = Transaction.objects.select_related('customer').order_by('-created_at')[:10]

    # Recent loans
    recent_loans = Loan.objects.select_related('customer', 'loan_product').order_by('-created_at')[:5]

    context = {
        'total_customers': total_customers,
        'active_susu_accounts': active_susu,
        'total_contributions': total_contributions,
        'today_contributions': today_contributions,
        'total_withdrawals': total_withdrawals,
        'outstanding_loans': outstanding_loans,
        'total_disbursed': total_disbursed,
        'total_repayments': total_repayments,
        'overdue_count': overdue_count,
        'pending_applications': pending_applications,
        'contributions_chart': contributions_chart,
        'recent_transactions': recent_transactions,
        'recent_loans': recent_loans,
    }
    return render(request, 'dashboard/staff_dashboard.html', context)


def _customer_dashboard(request):
    if not hasattr(request.user, 'customer_profile'):
        logout(request)
        messages.error(request, 'Your account has no customer profile. Please contact support or register as a customer.')
        return redirect('login')

    customer = request.user.customer_profile
    susu_accounts = SusuAccount.objects.filter(customer=customer, status='ACTIVE')

    total_balance = susu_accounts.aggregate(total=Sum('current_balance'))['total'] or Decimal('0.00')

    total_contributions = Transaction.objects.filter(
        customer=customer,
        transaction_type='SUSU_CONTRIBUTION'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    total_withdrawals = Transaction.objects.filter(
        customer=customer,
        transaction_type='WITHDRAWAL'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    active_loan = Loan.objects.filter(
        customer=customer,
        status__in=['ACTIVE', 'DISBURSED']
    ).first()

    total_loan_repaid = Decimal('0.00')
    next_repayment = None
    if active_loan:
        total_loan_repaid = active_loan.total_amount - active_loan.outstanding_balance
        next_repayment = active_loan.repayment_schedules.filter(
            status__in=['PENDING', 'PARTIALLY_PAID']
        ).order_by('due_date').first()

    recent_transactions = Transaction.objects.filter(
        customer=customer
    ).order_by('-created_at')[:10]

    recent_notifications = []
    from apps.notifications.models import SMSNotification
    try:
        recent_notifications = SMSNotification.objects.filter(
            customer=customer
        ).order_by('-created_at')[:5]
    except Exception:
        pass

    context = {
        'customer': customer,
        'susu_accounts': susu_accounts,
        'total_balance': total_balance,
        'total_contributions': total_contributions,
        'total_withdrawals': total_withdrawals,
        'active_loan': active_loan,
        'total_loan_repaid': total_loan_repaid,
        'next_repayment': next_repayment,
        'recent_transactions': recent_transactions,
        'recent_notifications': recent_notifications,
        'payment_pending': request.GET.get('payment_pending') == '1',
    }
    return render(request, 'dashboard/customer_dashboard.html', context)
