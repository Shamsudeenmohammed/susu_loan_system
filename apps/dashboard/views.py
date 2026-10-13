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
    user = request.user
    if user.has_role('CUSTOMER'):
        return _customer_dashboard(request)
    if user.has_role('TEACHER'):
        return redirect('academics:teacher_dashboard')
    if user.has_role('HEAD_TEACHER'):
        return redirect('school_fees_dashboard')
    if user.has_role('PROPRIETOR'):
        return _proprietor_dashboard(request)
    return _staff_dashboard(request)


def _staff_dashboard(request):
    context = _savings_context()
    return render(request, 'dashboard/staff_dashboard.html', context)


def _savings_context():
    """Savings / loans / susu aggregates shared by staff and proprietor views."""
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

    savings_balance = (
        total_contributions - total_withdrawals + total_disbursed - total_repayments
    )

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

    return {
        'total_customers': total_customers,
        'active_susu_accounts': active_susu,
        'total_contributions': total_contributions,
        'today_contributions': today_contributions,
        'total_withdrawals': total_withdrawals,
        'savings_balance': savings_balance,
        'outstanding_loans': outstanding_loans,
        'total_disbursed': total_disbursed,
        'total_repayments': total_repayments,
        'overdue_count': overdue_count,
        'pending_applications': pending_applications,
        'contributions_chart': contributions_chart,
        'recent_transactions': recent_transactions,
        'recent_loans': recent_loans,
    }


def _school_fees_overview(request):
    """Combined or per-school fee aggregates for the proprietor dashboard.

    Counts are computed with plain ``COUNT``/``SUM`` aggregates over school-owned
    querysets (no joins that could multiply rows), so combining the two schools
    never double-counts. Staff figures only ever include users bound to a
    visible school, never global administrators.
    """
    from django.contrib.auth import get_user_model
    from apps.school_fees.models import (
        Student, StudentFeeAccount, FeePayment, SchoolClass,
    )
    from apps.schools.models import School
    from apps.schools.scoping import get_active_school, scope_queryset

    User = get_user_model()
    active = get_active_school(request)
    schools_qs = School.objects.filter(is_active=True)
    if active is not None:
        schools_qs = schools_qs.filter(pk=active.pk)

    per_school = []
    for s in schools_qs:
        agg = StudentFeeAccount.objects.filter(school=s).aggregate(
            fees=Sum('total_fees'), paid=Sum('amount_paid'))
        expected = agg['fees'] or Decimal('0.00')
        collected = agg['paid'] or Decimal('0.00')
        scoped_staff = (User.objects
                        .filter(school=s, is_active=True)
                        .exclude(role='CUSTOMER'))
        per_school.append({
            'school': s,
            'students': Student.objects.filter(school=s, is_active=True).count(),
            'teachers': scoped_staff.filter(role='HEAD_TEACHER').count(),
            'staff': scoped_staff.count(),
            'expected': expected,
            'collected': collected,
            'outstanding': expected - collected,
        })

    accounts = scope_queryset(StudentFeeAccount.objects.all(), request)
    combined = accounts.aggregate(fees=Sum('total_fees'), paid=Sum('amount_paid'))
    combined_expected = combined['fees'] or Decimal('0.00')
    combined_collected = combined['paid'] or Decimal('0.00')

    visible_staff = (User.objects
                     .filter(school__in=schools_qs, is_active=True)
                     .exclude(role='CUSTOMER'))

    # Legacy rows that still have no school. Only surfaced in the combined view
    # so the proprietor knows what to review (ambiguous assignments are never
    # guessed automatically).
    unassigned = None
    if active is None:
        ua = StudentFeeAccount.objects.filter(school__isnull=True).aggregate(
            fees=Sum('total_fees'), paid=Sum('amount_paid'))
        unassigned = {
            'classes': SchoolClass.objects.filter(school__isnull=True).count(),
            'students': Student.objects.filter(school__isnull=True).count(),
            'accounts': StudentFeeAccount.objects.filter(school__isnull=True).count(),
            'payments': FeePayment.objects.filter(school__isnull=True).count(),
            'expected': ua['fees'] or Decimal('0.00'),
            'collected': ua['paid'] or Decimal('0.00'),
        }

    return {
        'per_school': per_school,
        'combined_students': scope_queryset(
            Student.objects.filter(is_active=True), request).count(),
        'combined_teachers': visible_staff.filter(role='HEAD_TEACHER').count(),
        'combined_staff': visible_staff.count(),
        'combined_expected': combined_expected,
        'combined_collected': combined_collected,
        'combined_outstanding': combined_expected - combined_collected,
        'combined_payments': scope_queryset(FeePayment.objects.all(), request)
            .aggregate(t=Sum('amount'))['t'] or Decimal('0.00'),
        'unassigned': unassigned,
    }


def _proprietor_dashboard(request):
    context = _savings_context()
    context.update(_school_fees_overview(request))
    context.update(_academics_overview(request))
    return render(request, 'dashboard/proprietor_dashboard.html', context)


def _academics_overview(request):
    """Consolidated academic comparison across schools for the proprietor.

    Aggregates are computed per school (never by joining across schools) so
    combined figures cannot double count.
    """
    from decimal import Decimal as _D

    from apps.academics.models import AttendanceRecord, MarkSubmission, Subject
    from apps.school_fees.models import AcademicYear, Term
    from apps.schools.models import School
    from apps.schools.scoping import get_active_school

    schools = School.objects.filter(is_active=True)
    active = get_active_school(request)
    if active is not None:
        schools = schools.filter(pk=active.pk)
    per_school = []
    for school in schools:
        year = AcademicYear.objects.filter(school=school).order_by('-start_date', '-name').first()
        term = Term.objects.filter(academic_year=year).order_by('term_number').first() if year else None
        attendance_pct = None
        if year is not None and term is not None:
            records = AttendanceRecord.objects.filter(
                session__school=school, session__academic_year=year, session__term=term)
            total = records.count()
            if total:
                attended = records.filter(status__in=['PRESENT', 'LATE']).count()
                attendance_pct = (Decimal(attended) / Decimal(total) * 100).quantize(Decimal('0.1'))
        per_school.append({
            'school': school,
            'subjects': Subject.objects.filter(school=school, is_active=True).count(),
            'submissions_pending': MarkSubmission.objects.filter(
                school=school,
                status=MarkSubmission.Status.SUBMITTED).count(),
            'attendance': attendance_pct,
        })
    return {'academics_overview': per_school}


def _customer_dashboard(request):
    if not hasattr(request.user, 'customer_profile'):
        logout(request)
        messages.error(request, 'Your account has no customer profile. Please contact support or register as a customer.')
        return redirect('login')

    customer = request.user.customer_profile

    if customer.is_pending:
        logout(request)
        messages.error(
            request,
            'Your account is awaiting administrator approval. '
            'You will be able to sign in once it has been approved.',
        )
        return redirect('login')

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
