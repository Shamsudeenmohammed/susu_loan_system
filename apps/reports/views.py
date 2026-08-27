from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import csv
from django.http import HttpResponse
from apps.core.decorators import role_required
from apps.customers.models import Customer
from apps.payments.models import Transaction, Withdrawal
from apps.loans.models import Loan, LoanRepayment, RepaymentSchedule


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_index(request):
    return render(request, 'reports/report_index.html')


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def customer_report(request):
    customers = Customer.objects.all()
    context = {
        'customers': customers,
        'total': customers.count(),
    }
    return render(request, 'reports/customer_report.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def contributions_report(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    transactions = Transaction.objects.filter(
        transaction_type='SUSU_CONTRIBUTION'
    ).select_related('customer', 'created_by')

    if start_date:
        transactions = transactions.filter(created_at__date__gte=start_date)
    if end_date:
        transactions = transactions.filter(created_at__date__lte=end_date)

    total = transactions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    context = {
        'transactions': transactions[:200],
        'total': total,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'reports/contributions_report.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_report(request):
    status = request.GET.get('status')
    loans = Loan.objects.select_related('customer', 'loan_product', 'approved_by').all()

    if status:
        loans = loans.filter(status=status)

    context = {
        'loans': loans[:200],
        'status_choices': Loan.Status.choices,
    }
    return render(request, 'reports/loan_report.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def repayments_report(request):
    repayments = LoanRepayment.objects.select_related('loan', 'loan__customer', 'recorded_by').all()
    total = repayments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    context = {
        'repayments': repayments[:200],
        'total': total,
    }
    return render(request, 'reports/repayments_report.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def overdue_report(request):
    today = timezone.now().date()
    overdue = RepaymentSchedule.objects.filter(
        due_date__lt=today,
        status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue']
    ).select_related('loan', 'loan__customer')

    total_overdue = sum(
        (s.total_due - s.amount_paid) for s in overdue
    )

    context = {
        'overdue': overdue,
        'total_overdue': total_overdue,
    }
    return render(request, 'reports/overdue_report.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def daily_summary(request):
    today = timezone.now().date()

    contributions = Transaction.objects.filter(
        transaction_type='SUSU_CONTRIBUTION',
        created_at__date=today
    )
    total_contributions = contributions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    num_contributions = contributions.count()

    withdrawals = Transaction.objects.filter(
        transaction_type='WITHDRAWAL',
        created_at__date=today
    )
    total_withdrawals = withdrawals.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    repayments = Transaction.objects.filter(
        transaction_type='LOAN_REPAYMENT',
        created_at__date=today
    )
    total_repayments = repayments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    context = {
        'today': today,
        'total_contributions': total_contributions,
        'num_contributions': num_contributions,
        'total_withdrawals': total_withdrawals,
        'total_repayments': total_repayments,
        'contributions': contributions.select_related('customer', 'created_by'),
        'withdrawals': withdrawals.select_related('customer'),
        'repayments': repayments.select_related('customer'),
    }
    return render(request, 'reports/daily_summary.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def export_csv(request):
    report_type = request.GET.get('type', 'transactions')

    response = HttpResponse(content_type='text/csv')

    if report_type == 'transactions':
        response['Content-Disposition'] = 'attachment; filename="transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(['Transaction #', 'Customer', 'Type', 'Amount', 'Balance Before', 'Balance After', 'Method', 'Date'])
        for txn in Transaction.objects.select_related('customer').all()[:1000]:
            writer.writerow([
                txn.transaction_number,
                txn.customer.get_full_name(),
                txn.get_transaction_type_display(),
                txn.amount,
                txn.balance_before,
                txn.balance_after,
                txn.get_payment_method_display(),
                txn.created_at.strftime('%Y-%m-%d %H:%M'),
            ])
    elif report_type == 'customers':
        response['Content-Disposition'] = 'attachment; filename="customers.csv"'
        writer = csv.writer(response)
        writer.writerow(['Customer #', 'Name', 'Phone', 'Email', 'Status', 'Registered'])
        for c in Customer.objects.all():
            writer.writerow([c.customer_number, c.get_full_name(), c.phone, c.email, c.status, c.created_at.strftime('%Y-%m-%d')])
    elif report_type == 'loans':
        response['Content-Disposition'] = 'attachment; filename="loans.csv"'
        writer = csv.writer(response)
        writer.writerow(['Loan #', 'Customer', 'Product', 'Principal', 'Interest', 'Outstanding', 'Status'])
        for loan in Loan.objects.select_related('customer', 'loan_product').all():
            writer.writerow([
                loan.loan_number, loan.customer.get_full_name(), loan.loan_product.name,
                loan.principal_amount, loan.interest_amount, loan.outstanding_balance, loan.status
            ])

    return response
