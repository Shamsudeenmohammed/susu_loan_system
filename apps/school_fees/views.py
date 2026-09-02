import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from apps.core.decorators import role_required

from .models import (
    SchoolClass,
    AcademicYear,
    Term,
    Student,
    FeeCategory,
    FeeStructure,
    StudentFeeAccount,
    FeePayment,
    ReminderTemplate,
    ReminderLog,
)
from .forms import (
    SchoolClassForm,
    AcademicYearForm,
    TermForm,
    FeeCategoryForm,
    FeeStructureForm,
    StudentForm,
    FeePaymentForm,
    FeeAccountForm,
    ReminderTemplateForm,
)
from .services import accounts as account_service
from .services import payments as payment_service
from .services import reminders as reminder_service

STAFF_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER')


def home_path():
    return redirect('school_fees_dashboard')


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def dashboard(request):
    today = timezone.now().date()

    year_pk = request.GET.get('academic_year')
    term_pk = request.GET.get('term')
    class_pk = request.GET.get('school_class')
    status = request.GET.get('status')

    accounts = StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term')

    if year_pk:
        accounts = accounts.filter(academic_year_id=year_pk)
    if term_pk:
        accounts = accounts.filter(term_id=term_pk)
    if class_pk:
        accounts = accounts.filter(student__school_class_id=class_pk)

    total_students = Student.objects.filter(is_active=True).count()

    # Outstanding balance requires per-row computation; aggregate total fees and paid
    total_fees = accounts.aggregate(total=Sum('total_fees'))['total'] or Decimal('0.00')
    total_collected = accounts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    counts = accounts.values('status').annotate(n=Count('id'))

    # Compute payment status counts including computed statuses
    full_paid = accounts.filter(status='FULLY_PAID').count()
    partial = accounts.filter(status='PARTIALLY_PAID').count()
    not_paid = accounts.filter(status__in=['NOT_PAID', 'PARTIALLY_PAID']).count()
    unpaid = accounts.filter(status='NOT_PAID').count()
    overdue = sum(1 for a in accounts if a.is_overdue)

    status_choices = StudentFeeAccount.PaymentStatus.choices

    # Chart: collections by month (from payments)
    monthly = (FeePayment.objects
               .annotate(month=TruncMonth('payment_date'))
               .values('month')
               .annotate(total=Sum('amount'))
               .order_by('month'))
    monthly_chart = []
    for m in monthly:
        month_val = m.get('month') if isinstance(m, dict) else None
        if month_val is not None and hasattr(month_val, 'strftime'):
            label = month_val.strftime('%Y-%m')
        else:
            label = str(month_val) if month_val else ''
        try:
            amount = float(m.get('total') if isinstance(m, dict) else getattr(m, 'total', 0))
        except (TypeError, ValueError):
            amount = 0.0
        monthly_chart.append({'label': label, 'amount': amount})

    # Chart: distribution by status
    status_counts = {c['status']: c['n'] for c in counts}
    status_chart = [
        {'label': label, 'value': status_counts.get(value, 0)}
        for value, label in status_choices
    ]

    context = {
        'total_students': total_students,
        'total_fees': total_fees,
        'total_collected': total_collected,
        'total_outstanding': total_fees - total_collected,
        'full_paid': full_paid,
        'partial': partial,
        'unpaid': unpaid,
        'overdue': overdue,
        'status_chart': status_chart,
        'monthly_chart': monthly_chart,
        'academic_years': AcademicYear.objects.all(),
        'terms': Term.objects.select_related('academic_year'),
        'school_classes': SchoolClass.objects.all(),
        'selected_year': year_pk,
        'selected_term': term_pk,
        'selected_class': class_pk,
        'selected_status': status,
    }
    return render(request, 'school_fees/dashboard.html', context)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def student_list(request):
    students = Student.objects.select_related('school_class').filter(is_active=True)
    query = request.GET.get('q')
    class_pk = request.GET.get('school_class')
    if query:
        students = students.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(student_id__icontains=query) | Q(parent_name__icontains=query)
            | Q(parent_phone__icontains=query))
    if class_pk:
        students = students.filter(school_class_id=class_pk)
    context = {
        'students': students,
        'school_classes': SchoolClass.objects.filter(is_active=True),
        'selected_class': class_pk,
        'query': query,
    }
    return render(request, 'school_fees/student_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def student_create(request):
    if request.method == 'POST':
        form = StudentForm(request.POST)
        if form.is_valid():
            student = form.save(commit=False)
            student.created_by = request.user
            student.save()
            account_service.create_fee_accounts_for_student(student)
            messages.success(request, f'Student {student.get_full_name()} created.')
            return redirect('school_fees_student_detail', pk=student.pk)
    else:
        form = StudentForm()
    return render(request, 'school_fees/student_form.html', {'form': form, 'title': 'New Student'})


@login_required
@role_required(*STAFF_ROLES)
def student_update(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.success(request, 'Student updated.')
            return redirect('school_fees_student_detail', pk=student.pk)
    else:
        form = StudentForm(instance=student)
    return render(request, 'school_fees/student_form.html',
                  {'form': form, 'student': student, 'title': 'Edit Student'})


@login_required
@role_required(*STAFF_ROLES)
def student_detail(request, pk):
    student = get_object_or_404(
        Student.objects.select_related('school_class'), pk=pk)
    accounts = student.fee_accounts.select_related('academic_year', 'term')
    payments = student.fee_payments.select_related('account', 'recorded_by')
    context = {
        'student': student,
        'accounts': accounts,
        'payments': payments,
        'terms': Term.objects.select_related('academic_year'),
    }
    return render(request, 'school_fees/student_detail.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def student_delete(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if request.method == 'POST':
        pid = student.pk
        student.delete()
        messages.success(request, 'Student deleted.')
        return redirect('school_fees_student_list')
    return redirect('school_fees_student_detail', pk=pk)


# ---------------------------------------------------------------------------
# Setups: classes, academic years, terms, fee categories, fee structures
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def class_list(request):
    classes = SchoolClass.objects.all()
    return render(request, 'school_fees/class_list.html', {'classes': classes})


@login_required
@role_required(*STAFF_ROLES)
def class_create(request):
    if request.method == 'POST':
        form = SchoolClassForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Class created.')
            return redirect('school_fees_class_list')
    else:
        form = SchoolClassForm()
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Class', 'back_url': 'school_fees_class_list'})


@login_required
@role_required(*STAFF_ROLES)
def class_update(request, pk):
    obj = get_object_or_404(SchoolClass, pk=pk)
    if request.method == 'POST':
        form = SchoolClassForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Class updated.')
            return redirect('school_fees_class_list')
    else:
        form = SchoolClassForm(instance=obj)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Class', 'back_url': 'school_fees_class_list'})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_list(request):
    years = AcademicYear.objects.all()
    return render(request, 'school_fees/academic_year_list.html', {'years': years})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_create(request):
    if request.method == 'POST':
        form = AcademicYearForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Academic year created.')
            return redirect('school_fees_academic_year_list')
    else:
        form = AcademicYearForm()
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Academic Year',
                   'back_url': 'school_fees_academic_year_list'})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_update(request, pk):
    obj = get_object_or_404(AcademicYear, pk=pk)
    if request.method == 'POST':
        form = AcademicYearForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Academic year updated.')
            return redirect('school_fees_academic_year_list')
    else:
        form = AcademicYearForm(instance=obj)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Academic Year',
                   'back_url': 'school_fees_academic_year_list'})


@login_required
@role_required(*STAFF_ROLES)
def term_list(request):
    terms = Term.objects.select_related('academic_year')
    return render(request, 'school_fees/term_list.html', {'terms': terms})


@login_required
@role_required(*STAFF_ROLES)
def term_create(request):
    if request.method == 'POST':
        form = TermForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Term created.')
            return redirect('school_fees_term_list')
    else:
        form = TermForm()
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Term', 'back_url': 'school_fees_term_list'})


@login_required
@role_required(*STAFF_ROLES)
def term_update(request, pk):
    obj = get_object_or_404(Term, pk=pk)
    if request.method == 'POST':
        form = TermForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Term updated.')
            return redirect('school_fees_term_list')
    else:
        form = TermForm(instance=obj)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Term', 'back_url': 'school_fees_term_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_category_list(request):
    categories = FeeCategory.objects.all()
    return render(request, 'school_fees/fee_category_list.html', {'categories': categories})


@login_required
@role_required(*STAFF_ROLES)
def fee_category_create(request):
    if request.method == 'POST':
        form = FeeCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Fee category created.')
            return redirect('school_fees_fee_category_list')
    else:
        form = FeeCategoryForm()
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Fee Category',
                   'back_url': 'school_fees_fee_category_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_list(request):
    structures = FeeStructure.objects.select_related(
        'academic_year', 'term', 'school_class', 'fee_category')
    year_pk = request.GET.get('academic_year')
    class_pk = request.GET.get('school_class')
    term_pk = request.GET.get('term')
    if year_pk:
        structures = structures.filter(academic_year_id=year_pk)
    if term_pk:
        structures = structures.filter(term_id=term_pk)
    if class_pk:
        structures = structures.filter(school_class_id=class_pk)
    context = {
        'structures': structures,
        'academic_years': AcademicYear.objects.all(),
        'terms': Term.objects.select_related('academic_year'),
        'school_classes': SchoolClass.objects.all(),
        'selected_year': year_pk,
        'selected_term': term_pk,
        'selected_class': class_pk,
    }
    return render(request, 'school_fees/fee_structure_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_create(request):
    if request.method == 'POST':
        form = FeeStructureForm(request.POST)
        if form.is_valid():
            fs = form.save(commit=False)
            fs.created_by = request.user
            fs.save()
            # Create accounts for all existing students in that class + term
            students = Student.objects.filter(school_class=fs.school_class, is_active=True)
            for s in students:
                account_service.get_or_create_fee_account(s, fs.term)
            messages.success(request, 'Fee structure saved and fee accounts updated.')
            return redirect('school_fees_fee_structure_list')
    else:
        form = FeeStructureForm()
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Fee Structure',
                   'back_url': 'school_fees_fee_structure_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_update(request, pk):
    obj = get_object_or_404(FeeStructure, pk=pk)
    if request.method == 'POST':
        form = FeeStructureForm(request.POST, instance=obj)
        if form.is_valid():
            fs = form.save(commit=False)
            fs.created_by = request.user
            fs.save()
            students = Student.objects.filter(school_class=fs.school_class, is_active=True)
            for s in students:
                account_service.get_or_create_fee_account(s, fs.term)
            messages.success(request, 'Fee structure updated and fee accounts refreshed.')
            return redirect('school_fees_fee_structure_list')
    else:
        form = FeeStructureForm(instance=obj)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Fee Structure',
                   'back_url': 'school_fees_fee_structure_list'})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def fee_structure_delete(request, pk):
    obj = get_object_or_404(FeeStructure, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Fee structure deleted.')
        return redirect('school_fees_fee_structure_list')
    return redirect('school_fees_fee_structure_list')


# ---------------------------------------------------------------------------
# Student Fee Accounts
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def fee_account_list(request):
    accounts = StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term')
    year_pk = request.GET.get('academic_year')
    term_pk = request.GET.get('term')
    class_pk = request.GET.get('school_class')
    status = request.GET.get('status')
    query = request.GET.get('q')

    accounts = accounts.select_related('student__school_class')

    if year_pk:
        accounts = accounts.filter(academic_year_id=year_pk)
    if term_pk:
        accounts = accounts.filter(term_id=term_pk)
    if class_pk:
        accounts = accounts.filter(student__school_class_id=class_pk)
    if status:
        if status == 'OVERDUE':
            account_ids = [a.pk for a in accounts if a.is_overdue]
            accounts = StudentFeeAccount.objects.filter(pk__in=account_ids)
        else:
            accounts = accounts.filter(status=status)
    if query:
        accounts = accounts.filter(
            Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(student__student_id__icontains=query)
            | Q(student__parent_name__icontains=query)
            | Q(student__parent_phone__icontains=query))

    context = {
        'accounts': accounts,
        'academic_years': AcademicYear.objects.all(),
        'terms': Term.objects.select_related('academic_year'),
        'school_classes': SchoolClass.objects.all(),
        'status_choices': StudentFeeAccount.PaymentStatus.choices,
        'selected_year': year_pk,
        'selected_term': term_pk,
        'selected_class': class_pk,
        'selected_status': status,
        'query': query,
    }
    return render(request, 'school_fees/fee_account_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def fee_account_detail(request, pk):
    account = get_object_or_404(
        StudentFeeAccount.objects.select_related(
            'student', 'student__school_class', 'academic_year', 'term'), pk=pk)
    account.recalculate_status()
    account.refresh_from_db()
    payments = account.payments.select_related('recorded_by')
    context = {'account': account, 'payments': payments}
    return render(request, 'school_fees/fee_account_detail.html', context)


@login_required
@role_required(*STAFF_ROLES)
def fee_account_update(request, pk):
    account = get_object_or_404(StudentFeeAccount, pk=pk)
    if request.method == 'POST':
        form = FeeAccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            account.recalculate_status()
            messages.success(request, 'Fee account updated.')
            return redirect('school_fees_fee_account_detail', pk=account.pk)
    else:
        form = FeeAccountForm(instance=account)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Fee Account',
                   'back_url': 'school_fees_fee_account_detail',
                   'back_arg': account.pk})


# ---------------------------------------------------------------------------
# Fee Payments
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def payment_list(request):
    payments = FeePayment.objects.select_related(
        'student', 'student__school_class', 'account', 'recorded_by')
    query = request.GET.get('q')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    method = request.GET.get('method')

    if query:
        payments = payments.filter(
            Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(receipt_number__icontains=query)
            | Q(reference__icontains=query))
    if start_date:
        payments = payments.filter(payment_date__gte=start_date)
    if end_date:
        payments = payments.filter(payment_date__lte=end_date)
    if method:
        payments = payments.filter(payment_method=method)

    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {
        'payments': payments,
        'total': total,
        'query': query,
        'start_date': start_date,
        'end_date': end_date,
        'method': method,
        'method_choices': FeePayment.PaymentMethod.choices,
    }
    return render(request, 'school_fees/payment_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def payment_create(request, pk=None):
    account_pk = pk or request.GET.get('account') or request.POST.get('account')
    account = None
    if account_pk:
        account = get_object_or_404(
            StudentFeeAccount.objects.select_related('student', 'student__school_class',
                                                     'academic_year', 'term'),
            pk=account_pk)
        account.recalculate_status()
        account.refresh_from_db()

    if request.method == 'POST':
        form = FeePaymentForm(request.POST)
        account = get_object_or_404(StudentFeeAccount, pk=request.POST.get('account'))
        account.recalculate_status()
        account.refresh_from_db()
        if form.is_valid():
            try:
                payment = payment_service.record_payment(
                    account=account,
                    amount=form.cleaned_data['amount'],
                    payment_method=form.cleaned_data['payment_method'],
                    reference=form.cleaned_data['reference'],
                    recorded_by=request.user,
                    note=form.cleaned_data['note'],
                )
                messages.success(request, f'Payment recorded. Receipt: {payment.receipt_number}')
                return redirect('school_fees_receipt', pk=payment.pk)
            except payment_service.FeePaymentError as e:
                form.add_error(None, e.message)
    else:
        form = FeePaymentForm()

    accounts = StudentFeeAccount.objects.select_related(
        'student', 'academic_year', 'term')
    context = {
        'form': form,
        'account': account,
        'accounts': accounts,
    }
    return render(request, 'school_fees/payment_form.html', context)


# ---------------------------------------------------------------------------
# Outstanding fees
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def outstanding_fees(request):
    accounts = StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term')
    accounts = [a for a in accounts if a.outstanding_balance > 0]
    total_outstanding = sum(a.outstanding_balance for a in accounts)

    class_pk = request.GET.get('school_class')
    if class_pk:
        accounts = [a for a in accounts if a.student.school_class_id == int(class_pk) if a.outstanding_balance > 0]

    context = {
        'accounts': accounts,
        'total_outstanding': total_outstanding,
        'school_classes': SchoolClass.objects.all(),
        'selected_class': class_pk,
    }
    return render(request, 'school_fees/outstanding_fees.html', context)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def receipt(request, pk):
    payment = get_object_or_404(
        FeePayment.objects.select_related(
            'student', 'student__school_class', 'account',
            'academic_year', 'term', 'recorded_by'), pk=pk)
    return render(request, 'school_fees/receipt.html', {'payment': payment})


@login_required
@role_required(*STAFF_ROLES)
def payment_detail(request, pk):
    payment = get_object_or_404(
        FeePayment.objects.select_related(
            'student', 'student__school_class', 'account',
            'academic_year', 'term', 'recorded_by'), pk=pk)
    return render(request, 'school_fees/payment_detail.html', {'payment': payment})


# ---------------------------------------------------------------------------
# Online payment (Paystack)
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def pay_online(request, pk):
    account = get_object_or_404(
        StudentFeeAccount.objects.select_related('student', 'student__school_class'), pk=pk)
    account.recalculate_status()
    account.refresh_from_db()

    amount = request.POST.get('amount')
    try:
        amount = Decimal(str(amount)) if amount else account.outstanding_balance
    except Exception:
        amount = account.outstanding_balance

    email = request.POST.get('email') or account.student.parent_email

    from django.conf import settings
    callback_url = f"{settings.SITE_URL}/school-fees/payment/callback/"

    try:
        result = payment_service.initialize_fee_payment(account, amount, email, callback_url)
    except payment_service.FeePaymentError as e:
        messages.error(request, e.message)
        return redirect('school_fees_fee_account_detail', pk=account.pk)

    if not result.get('status'):
        messages.error(request, result.get('message', 'Payment initialization failed.'))
        return redirect('school_fees_fee_account_detail', pk=account.pk)

    return redirect(result['authorization_url'])


@login_required
def payment_callback(request):
    from django.conf import settings
    reference = request.GET.get('reference', '')
    account_id = request.GET.get('account')
    if not reference:
        messages.error(request, 'Payment could not be verified.')
        return home_path()

    account = get_object_or_404(StudentFeeAccount, pk=account_id) if account_id else None

    if account is None:
        # Find the account via paystack reference stored on an existing payment
        existing = FeePayment.objects.filter(paystack_reference=reference).first()
        if existing:
            messages.success(request, 'Payment verified.')
            return redirect('school_fees_receipt', pk=existing.pk)
        messages.error(request, 'Could not match the payment to a fee account.')
        return home_path()

    try:
        payment = payment_service.verify_and_credit(account, reference, request.user)
        messages.success(request, f'Payment verified. Receipt: {payment.receipt_number}')
        return redirect('school_fees_receipt', pk=payment.pk)
    except payment_service.FeePaymentError as e:
        messages.error(request, e.message)
        return redirect('school_fees_fee_account_detail', pk=account.pk)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def reminder_template_list(request):
    templates = ReminderTemplate.objects.all()
    return render(request, 'school_fees/reminder_template_list.html', {'templates': templates})


@login_required
@role_required(*STAFF_ROLES)
def reminder_template_edit(request, pk):
    obj = get_object_or_404(ReminderTemplate, pk=pk)
    if request.method == 'POST':
        form = ReminderTemplateForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Reminder template updated.')
            return redirect('school_fees_reminder_template_list')
    else:
        form = ReminderTemplateForm(instance=obj)
    return render(request, 'school_fees/reminder_template_form.html', {'form': form})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def send_reminders(request):
    """Send reminders to a selected set of accounts."""
    if request.method == 'POST':
        account_ids = request.POST.getlist('accounts')
        reminder_type = request.POST.get('reminder_type')
        if not account_ids or not reminder_type:
            messages.error(request, 'Select at least one account and a reminder type.')
            return redirect('school_fees_send_reminders')

        accounts = StudentFeeAccount.objects.filter(pk__in=account_ids)
        sent = reminder_service.send_reminders_for_accounts(
            accounts, reminder_type, sent_by=request.user)
        success = sum(1 for s in sent if s.status == ReminderLog.Status.SENT)
        messages.success(request, f'Sent {success} reminder(s).')
        return redirect('school_fees_reminder_log')

    accounts = StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term')
    accounts = [a for a in accounts if a.outstanding_balance > 0]
    context = {
        'accounts': accounts,
        'reminder_types': ReminderTemplate.ReminderType.choices,
        'templates': ReminderTemplate.objects.all(),
    }
    return render(request, 'school_fees/send_reminders.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def send_reminders_by_class(request):
    if request.method == 'POST':
        class_pk = request.POST.get('school_class')
        term_pk = request.POST.get('term')
        reminder_type = request.POST.get('reminder_type')
        if not class_pk or not reminder_type:
            messages.error(request, 'Select a class and reminder type.')
            return redirect('school_fees_send_reminders_by_class')

        accounts = StudentFeeAccount.objects.filter(
            student__school_class_id=class_pk)
        if term_pk:
            accounts = accounts.filter(term_id=term_pk)
        accounts = [a for a in accounts if a.outstanding_balance > 0]
        if not accounts:
            messages.error(request, 'No outstanding accounts found for the selected class.')
            return redirect('school_fees_send_reminders_by_class')

        sent = reminder_service.send_reminders_for_accounts(
            accounts, reminder_type, sent_by=request.user)
        success = sum(1 for s in sent if s.status == ReminderLog.Status.SENT)
        messages.success(request, f'Sent {success} reminder(s) to class.')
        return redirect('school_fees_reminder_log')

    context = {
        'school_classes': SchoolClass.objects.all(),
        'terms': Term.objects.select_related('academic_year'),
        'reminder_types': ReminderTemplate.ReminderType.choices,
    }
    return render(request, 'school_fees/send_reminders_class.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def send_overdue_reminders(request):
    """Send overdue reminders to all overdue accounts."""
    if request.method == 'POST':
        accounts = [a for a in StudentFeeAccount.objects.all() if a.is_overdue]
        if not accounts:
            messages.error(request, 'No overdue accounts found.')
            return redirect('school_fees_fee_account_list')
        sent = reminder_service.send_reminders_for_accounts(
            accounts, ReminderTemplate.ReminderType.OVERDUE, sent_by=request.user)
        success = sum(1 for s in sent if s.status == ReminderLog.Status.SENT)
        messages.success(request, f'Sent {success} overdue reminder(s).')
        return redirect('school_fees_reminder_log')
    overdue_accounts = [a for a in StudentFeeAccount.objects.select_related(
        'student', 'student__school_class') if a.is_overdue]
    return render(request, 'school_fees/send_overdue_reminders.html',
                  {'overdue_accounts': overdue_accounts})


@login_required
@role_required(*STAFF_ROLES)
def reminder_log(request):
    logs = ReminderLog.objects.select_related('student', 'sent_by')
    query = request.GET.get('q')
    reminder_type = request.GET.get('type')
    status = request.GET.get('status')
    if query:
        logs = logs.filter(
            Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(parent_phone__icontains=query))
    if reminder_type:
        logs = logs.filter(reminder_type=reminder_type)
    if status:
        logs = logs.filter(status=status)
    context = {
        'logs': logs,
        'reminder_types': ReminderTemplate.ReminderType.choices,
        'query': query,
        'selected_type': reminder_type,
        'selected_status': status,
    }
    return render(request, 'school_fees/reminder_log.html', context)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def reports(request):
    return render(request, 'school_fees/reports.html',
                  {'academic_years': AcademicYear.objects.all(),
                   'status_choices': StudentFeeAccount.PaymentStatus.choices})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_daily_payments(request):
    date = request.GET.get('date') or timezone.now().date()
    payments = FeePayment.objects.filter(payment_date=date).select_related(
        'student', 'student__school_class', 'recorded_by')
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'date': date,
               'title': 'Daily Payments'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_monthly_payments(request):
    month = request.GET.get('month') or timezone.now().strftime('%Y-%m')
    payments = FeePayment.objects.filter(
        payment_date__startswith=month).select_related(
        'student', 'student__school_class', 'recorded_by')
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'month': month,
               'title': 'Monthly Payments'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_year_collections(request):
    year_pk = request.GET.get('academic_year')
    year = get_object_or_404(AcademicYear, pk=year_pk) if year_pk else None
    payments = FeePayment.objects.all()
    if year:
        payments = payments.filter(academic_year=year)
    payments = payments.select_related('student', 'student__school_class', 'term')
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'year': year,
               'academic_years': AcademicYear.objects.all(), 'title': 'Academic Year Collections'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_outstanding(request):
    accounts = [a for a in StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term') if a.outstanding_balance > 0]
    total = sum(a.outstanding_balance for a in accounts)
    context = {'accounts': accounts, 'total': total, 'title': 'Outstanding Fees'}
    return render(request, 'school_fees/report_outstanding.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_overdue(request):
    accounts = [a for a in StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term') if a.is_overdue]
    total = sum(a.outstanding_balance for a in accounts)
    context = {'accounts': accounts, 'total': total, 'title': 'Overdue Fees'}
    return render(request, 'school_fees/report_outstanding.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_status(request):
    status = request.GET.get('status', 'FULLY_PAID')
    if status == 'OVERDUE':
        accounts = [a for a in StudentFeeAccount.objects.select_related(
            'student', 'student__school_class', 'academic_year', 'term') if a.is_overdue]
    else:
        accounts = StudentFeeAccount.objects.filter(status=status).select_related(
            'student', 'student__school_class', 'academic_year', 'term')
    context = {'accounts': accounts, 'status': status,
               'status_choices': StudentFeeAccount.PaymentStatus.choices,
               'title': f'{status.replace("_", " ").title()} Students'}
    return render(request, 'school_fees/report_status.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def report_class_collections(request):
    class_pk = request.GET.get('school_class')
    structures = FeeStructure.objects.filter(is_active=True).select_related(
        'school_class', 'academic_year', 'term', 'fee_category')
    if class_pk:
        structures = structures.filter(school_class_id=class_pk)
    reports = []
    for sc in SchoolClass.objects.all():
        row = {
            'school_class': sc,
            'expected': Decimal('0.00'),
            'collected': Decimal('0.00'),
            'outstanding': Decimal('0.00'),
        }
        accounts = StudentFeeAccount.objects.filter(student__school_class=sc)
        row['expected'] = accounts.aggregate(total=Sum('total_fees'))['total'] or Decimal('0.00')
        row['collected'] = accounts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        row['outstanding'] = row['expected'] - row['collected']
        reports.append(row)
    if class_pk:
        reports = [r for r in reports if str(r['school_class'].pk) == class_pk]
    total_expected = sum(r['expected'] for r in reports)
    total_collected = sum(r['collected'] for r in reports)
    total_outstanding = sum(r['outstanding'] for r in reports)
    context = {
        'reports': reports,
        'school_classes': SchoolClass.objects.all(),
        'selected_class': class_pk,
        'total_expected': total_expected,
        'total_collected': total_collected,
        'total_outstanding': total_outstanding,
        'title': 'Class/Program Fee Collections',
    }
    return render(request, 'school_fees/report_class_collections.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def export_fees_csv(request):
    report_type = request.GET.get('type', 'payments')
    response = HttpResponse(content_type='text/csv')
    if report_type == 'payments':
        response['Content-Disposition'] = 'attachment; filename="fee_payments.csv"'
        writer = csv.writer(response)
        writer.writerow(['Receipt #', 'Student ID', 'Student', 'Class', 'Amount',
                         'Method', 'Reference', 'Date', 'Recorded By'])
        for p in FeePayment.objects.select_related('student', 'student__school_class', 'recorded_by'):
            writer.writerow([
                p.receipt_number, p.student.student_id, p.student.get_full_name(),
                p.student.school_class.name, p.amount, p.get_payment_method_display(),
                p.reference, p.payment_date, (p.recorded_by.get_full_name() if p.recorded_by else ''),
            ])
    elif report_type == 'accounts':
        response['Content-Disposition'] = 'attachment; filename="fee_accounts.csv"'
        writer = csv.writer(response)
        writer.writerow(['Account #', 'Student ID', 'Student', 'Class', 'Year', 'Term',
                         'Total Fees', 'Amount Paid', 'Outstanding', 'Status'])
        for a in StudentFeeAccount.objects.select_related(
                'student', 'student__school_class', 'academic_year', 'term'):
            writer.writerow([
                a.account_number, a.student.student_id, a.student.get_full_name(),
                a.student.school_class.name, a.academic_year.name, a.term.name,
                a.total_fees, a.amount_paid, a.outstanding_balance, a.status,
            ])
    elif report_type == 'outstanding':
        response['Content-Disposition'] = 'attachment; filename="outstanding_fees.csv"'
        writer = csv.writer(response)
        writer.writerow(['Account #', 'Student ID', 'Student', 'Class', 'Year', 'Term',
                         'Total Fees', 'Amount Paid', 'Outstanding'])
        for a in StudentFeeAccount.objects.select_related(
                'student', 'student__school_class', 'academic_year', 'term'):
            if a.outstanding_balance > 0:
                writer.writerow([
                    a.account_number, a.student.student_id, a.student.get_full_name(),
                    a.student.school_class.name, a.academic_year.name, a.term.name,
                    a.total_fees, a.amount_paid, a.outstanding_balance,
                ])
    return response
