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
from apps.schools.scoping import (
    can_access_school,
    get_active_school,
    scope_queryset,
    scope_queryset_by_path,
    scope_reference_queryset,
)

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
    AssignFeeForm,
)
from .services import accounts as account_service
from .services import payments as payment_service
from .services import reminders as reminder_service

STAFF_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER', 'HEAD_TEACHER')


def home_path():
    return redirect('school_fees_dashboard')


def _school(request):
    """The school currently in focus for this request (None = all schools)."""
    return get_active_school(request)


def _active_school(request):
    return _school(request)


def _school_denied(request, obj):
    """Return True (and warn) when ``obj`` is outside the user's school scope.

    Used as a final, server-side check on create/update so tampered form fields
    can never assign a record to a school the user may not touch.
    """
    if not can_access_school(request.user, getattr(obj, 'school', None)):
        messages.error(
            request,
            'That record belongs to a different school. '
            'You can only manage records for your assigned school.')
        return True
    return False


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

    accounts = scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request)

    if year_pk:
        accounts = accounts.filter(academic_year_id=year_pk)
    if term_pk:
        accounts = accounts.filter(term_id=term_pk)
    if class_pk:
        accounts = accounts.filter(student__school_class_id=class_pk)

    total_students = scope_queryset(
        Student.objects.filter(is_active=True), request).count()

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
    monthly = (scope_queryset(FeePayment.objects, request)
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
        'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
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
    students = scope_queryset(
        Student.objects.select_related('school_class').filter(is_active=True), request)
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
        'school_classes': scope_queryset(
            SchoolClass.objects.filter(is_active=True), request),
        'selected_class': class_pk,
        'query': query,
    }
    return render(request, 'school_fees/student_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def student_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = StudentForm(request.POST, school=school)
        if form.is_valid():
            student = form.save(commit=False)
            student.created_by = request.user
            if student.school_id is None and school is not None:
                student.school = school
            if _school_denied(request, student):
                return redirect('school_fees_student_list')
            student.save()
            account_service.create_fee_accounts_for_student(student)
            messages.success(request, f'Student {student.get_full_name()} created.')
            return redirect('school_fees_student_detail', pk=student.pk)
    else:
        form = StudentForm(school=school)
    return render(request, 'school_fees/student_form.html', {'form': form, 'title': 'New Student'})


@login_required
@role_required(*STAFF_ROLES)
def student_update(request, pk):
    student = get_object_or_404(scope_queryset(Student.objects.all(), request), pk=pk)
    school = _school(request)
    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student, school=school)
        if form.is_valid():
            updated = form.save(commit=False)
            if _school_denied(request, updated):
                return redirect('school_fees_student_list')
            updated.save()
            messages.success(request, 'Student updated.')
            return redirect('school_fees_student_detail', pk=student.pk)
    else:
        form = StudentForm(instance=student, school=school)
    return render(request, 'school_fees/student_form.html',
                  {'form': form, 'student': student, 'title': 'Edit Student'})


@login_required
@role_required(*STAFF_ROLES)
def student_detail(request, pk):
    student = get_object_or_404(
        scope_queryset(Student.objects.select_related('school_class'), request), pk=pk)
    accounts = student.fee_accounts.select_related('academic_year', 'term')
    payments = student.fee_payments.select_related('account', 'recorded_by')
    context = {
        'student': student,
        'accounts': accounts,
        'payments': payments,
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
    }
    return render(request, 'school_fees/student_detail.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def student_delete(request, pk):
    student = get_object_or_404(scope_queryset(Student.objects.all(), request), pk=pk)
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
    classes = scope_queryset(SchoolClass.objects.all(), request)
    return render(request, 'school_fees/class_list.html', {'classes': classes})


@login_required
@role_required(*STAFF_ROLES)
def class_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = SchoolClassForm(request.POST, school=school)
        if form.is_valid():
            obj = form.save(commit=False)
            if _school_denied(request, obj):
                return redirect('school_fees_class_list')
            obj.save()
            messages.success(request, 'Class created.')
            return redirect('school_fees_class_list')
    else:
        form = SchoolClassForm(school=school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Class', 'back_url': 'school_fees_class_list'})


@login_required
@role_required(*STAFF_ROLES)
def class_update(request, pk):
    obj = get_object_or_404(scope_queryset(SchoolClass.objects.all(), request), pk=pk)
    if request.method == 'POST':
        form = SchoolClassForm(request.POST, instance=obj, school=obj.school)
        if form.is_valid():
            form.save()
            messages.success(request, 'Class updated.')
            return redirect('school_fees_class_list')
    else:
        form = SchoolClassForm(instance=obj, school=obj.school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Class', 'back_url': 'school_fees_class_list'})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_list(request):
    years = scope_reference_queryset(AcademicYear.objects.all(), request)
    return render(request, 'school_fees/academic_year_list.html', {'years': years})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = AcademicYearForm(request.POST, school=school)
        if form.is_valid():
            obj = form.save(commit=False)
            if _school_denied(request, obj):
                return redirect('school_fees_academic_year_list')
            obj.save()
            messages.success(request, 'Academic year created.')
            return redirect('school_fees_academic_year_list')
    else:
        form = AcademicYearForm(school=school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Academic Year',
                   'back_url': 'school_fees_academic_year_list'})


@login_required
@role_required(*STAFF_ROLES)
def academic_year_update(request, pk):
    obj = get_object_or_404(scope_reference_queryset(AcademicYear.objects.all(), request), pk=pk)
    if request.method == 'POST':
        form = AcademicYearForm(request.POST, instance=obj, school=obj.school)
        if form.is_valid():
            form.save()
            messages.success(request, 'Academic year updated.')
            return redirect('school_fees_academic_year_list')
    else:
        form = AcademicYearForm(instance=obj, school=obj.school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Academic Year',
                   'back_url': 'school_fees_academic_year_list'})


@login_required
@role_required(*STAFF_ROLES)
def term_list(request):
    terms = scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                           field='academic_year__school')
    return render(request, 'school_fees/term_list.html', {'terms': terms})


@login_required
@role_required(*STAFF_ROLES)
def term_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = TermForm(request.POST, school=school)
        if form.is_valid():
            form.save()
            messages.success(request, 'Term created.')
            return redirect('school_fees_term_list')
    else:
        form = TermForm(school=school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Term', 'back_url': 'school_fees_term_list'})


@login_required
@role_required(*STAFF_ROLES)
def term_update(request, pk):
    obj = get_object_or_404(
        scope_reference_queryset(Term.objects.all(), request, field='academic_year__school'), pk=pk)
    if request.method == 'POST':
        form = TermForm(request.POST, instance=obj, school=obj.academic_year.school)
        if form.is_valid():
            form.save()
            messages.success(request, 'Term updated.')
            return redirect('school_fees_term_list')
    else:
        form = TermForm(instance=obj, school=obj.academic_year.school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Term', 'back_url': 'school_fees_term_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_category_list(request):
    categories = scope_queryset(FeeCategory.objects.all(), request)
    return render(request, 'school_fees/fee_category_list.html', {'categories': categories})


@login_required
@role_required(*STAFF_ROLES)
def fee_category_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = FeeCategoryForm(request.POST, school=school)
        if form.is_valid():
            obj = form.save(commit=False)
            if _school_denied(request, obj):
                return redirect('school_fees_fee_category_list')
            obj.save()
            messages.success(request, 'Fee category created.')
            return redirect('school_fees_fee_category_list')
    else:
        form = FeeCategoryForm(school=school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Fee Category',
                   'back_url': 'school_fees_fee_category_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_list(request):
    structures = scope_queryset(FeeStructure.objects.select_related(
        'academic_year', 'term', 'school_class', 'fee_category'), request)
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
        'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
        'selected_year': year_pk,
        'selected_term': term_pk,
        'selected_class': class_pk,
    }
    return render(request, 'school_fees/fee_structure_list.html', context)


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_create(request):
    school = _school(request)
    if request.method == 'POST':
        form = FeeStructureForm(request.POST, school=school)
        if form.is_valid():
            fs = form.save(commit=False)
            fs.created_by = request.user
            if fs.school_id is None and school is not None:
                fs.school = school
            if _school_denied(request, fs):
                return redirect('school_fees_fee_structure_list')
            fs.save()
            # Create accounts for all existing students in that class + term
            students = Student.objects.filter(school_class=fs.school_class, is_active=True)
            for s in students:
                account_service.get_or_create_fee_account(s, fs.term)
            messages.success(request, 'Fee structure saved and fee accounts updated.')
            return redirect('school_fees_fee_structure_list')
    else:
        form = FeeStructureForm(school=school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'New Fee Structure',
                   'back_url': 'school_fees_fee_structure_list'})


@login_required
@role_required(*STAFF_ROLES)
def fee_structure_update(request, pk):
    obj = get_object_or_404(scope_queryset(FeeStructure.objects.all(), request), pk=pk)
    if request.method == 'POST':
        form = FeeStructureForm(request.POST, instance=obj, school=obj.school)
        if form.is_valid():
            fs = form.save(commit=False)
            fs.created_by = request.user
            if _school_denied(request, fs):
                return redirect('school_fees_fee_structure_list')
            fs.save()
            students = Student.objects.filter(school_class=fs.school_class, is_active=True)
            for s in students:
                account_service.get_or_create_fee_account(s, fs.term)
            messages.success(request, 'Fee structure updated and fee accounts refreshed.')
            return redirect('school_fees_fee_structure_list')
    else:
        form = FeeStructureForm(instance=obj, school=obj.school)
    return render(request, 'school_fees/setup_form.html',
                  {'form': form, 'title': 'Edit Fee Structure',
                   'back_url': 'school_fees_fee_structure_list'})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def fee_structure_delete(request, pk):
    obj = get_object_or_404(scope_queryset(FeeStructure.objects.all(), request), pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Fee structure deleted.')
        return redirect('school_fees_fee_structure_list')
    return redirect('school_fees_fee_structure_list')


# ---------------------------------------------------------------------------
# Assign fees
# ---------------------------------------------------------------------------
@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def assign_fee(request):
    """Assign a fee to all students, specific classes, or a single student."""
    school = _school(request)
    result = None
    if request.method == 'POST':
        form = AssignFeeForm(request.POST, school=school)
        if form.is_valid():
            scope = form.cleaned_data['scope']
            term = form.cleaned_data['term']
            fee_category = form.cleaned_data['fee_category']
            amount = form.cleaned_data['amount']
            due_date = form.cleaned_data['due_date']
            description = form.cleaned_data.get('description', '')
            chosen_school = form.cleaned_data.get('school') or school
            if chosen_school is not None and not can_access_school(request.user, chosen_school):
                messages.error(request, 'You cannot assign fees for that school.')
                return redirect('school_fees_assign_fee')

            # Claim any legacy (school-less) selections for the chosen branch.
            form.adopt_school(chosen_school)

            if scope == 'all':
                result = account_service.assign_fee_to_all(
                    term, fee_category, amount, due_date, description,
                    school=chosen_school)
                messages.success(
                    request,
                    f'Fee assigned to all active students across every class '
                    f'({len(result["accounts"])} account(s) updated).'
                )
            elif scope == 'classes':
                classes = form.cleaned_data['classes']
                result = account_service.assign_fee_to_classes(
                    term, classes, fee_category, amount, due_date, description)
                class_names = ', '.join(c.name for c in classes)
                messages.success(
                    request,
                    f'Fee assigned to {len(result["accounts"])} student(s) in: {class_names}.'
                )
            else:  # single
                student = form.cleaned_data['student']
                result = account_service.assign_fee_to_student(
                    student, term, fee_category, amount, due_date, description)
                messages.success(
                    request,
                    f'Fee assigned to {student.get_full_name()}.'
                )

            context = {
                'form': AssignFeeForm(school=school),
                'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
                'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                        field='academic_year__school'),
                'fee_categories': scope_queryset(
                    FeeCategory.objects.filter(is_active=True), request),
                'school_classes': scope_queryset(
                    SchoolClass.objects.filter(is_active=True), request),
                'result': result,
            }
            return render(request, 'school_fees/assign_fee.html', context)
    else:
        form = AssignFeeForm(school=school)

    context = {
        'form': form,
        'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
        'fee_categories': scope_queryset(FeeCategory.objects.filter(is_active=True), request),
        'school_classes': scope_queryset(SchoolClass.objects.filter(is_active=True), request),
    }
    return render(request, 'school_fees/assign_fee.html', context)


@login_required
@role_required(*STAFF_ROLES)
def student_autocomplete(request):
    """JSON endpoint for searching students (for the assign-fee single scope)."""
    query = request.GET.get('q', '').strip()
    students = scope_queryset(
        Student.objects.filter(is_active=True).select_related('school_class'), request)
    if query:
        students = students.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(student_id__icontains=query) | Q(parent_name__icontains=query))
    students = students[:20]
    from django.http import JsonResponse
    return JsonResponse({
        'results': [
            {
                'id': s.pk,
                'text': f"{s.student_id} - {s.get_full_name()} ({s.school_class.name})",
            }
            for s in students
        ]
    })


# ---------------------------------------------------------------------------
# Student Fee Accounts
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def fee_account_list(request):
    accounts = scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request)
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
        'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
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
        scope_queryset(StudentFeeAccount.objects.select_related(
            'student', 'student__school_class', 'academic_year', 'term'), request), pk=pk)
    account.recalculate_status()
    account.refresh_from_db()
    payments = account.payments.select_related('recorded_by')
    context = {'account': account, 'payments': payments}
    return render(request, 'school_fees/fee_account_detail.html', context)


@login_required
@role_required(*STAFF_ROLES)
def fee_account_update(request, pk):
    account = get_object_or_404(scope_queryset(StudentFeeAccount.objects.all(), request), pk=pk)
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
    payments = scope_queryset(FeePayment.objects.select_related(
        'student', 'student__school_class', 'account', 'recorded_by'), request)
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
            scope_queryset(StudentFeeAccount.objects.select_related(
                'student', 'student__school_class', 'academic_year', 'term'), request),
            pk=account_pk)
        account.recalculate_status()
        account.refresh_from_db()

    if request.method == 'POST':
        form = FeePaymentForm(request.POST)
        account = get_object_or_404(
            scope_queryset(StudentFeeAccount.objects.all(), request),
            pk=request.POST.get('account'))
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

    accounts = scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'academic_year', 'term'), request)
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
    accounts = scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request)
    accounts = [a for a in accounts if a.outstanding_balance > 0]
    total_outstanding = sum(a.outstanding_balance for a in accounts)

    class_pk = request.GET.get('school_class')
    if class_pk:
        accounts = [a for a in accounts if a.student.school_class_id == int(class_pk) if a.outstanding_balance > 0]

    context = {
        'accounts': accounts,
        'total_outstanding': total_outstanding,
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
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
        scope_queryset(FeePayment.objects.select_related(
            'student', 'student__school_class', 'account',
            'academic_year', 'term', 'recorded_by'), request), pk=pk)
    return render(request, 'school_fees/receipt.html', {'payment': payment})


@login_required
@role_required(*STAFF_ROLES)
def payment_detail(request, pk):
    payment = get_object_or_404(
        scope_queryset(FeePayment.objects.select_related(
            'student', 'student__school_class', 'account',
            'academic_year', 'term', 'recorded_by'), request), pk=pk)
    return render(request, 'school_fees/payment_detail.html', {'payment': payment})


# ---------------------------------------------------------------------------
# Online payment (Paystack)
# ---------------------------------------------------------------------------
@login_required
@role_required(*STAFF_ROLES)
def pay_online(request, pk):
    account = get_object_or_404(
        scope_queryset(StudentFeeAccount.objects.select_related(
            'student', 'student__school_class'), request), pk=pk)
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

    account = get_object_or_404(
        scope_queryset(StudentFeeAccount.objects.all(), request),
        pk=account_id) if account_id else None

    if account is None:
        # Find the account via paystack reference stored on an existing payment
        existing = scope_queryset(
            FeePayment.objects.all(), request).filter(paystack_reference=reference).first()
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
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def send_reminders(request):
    """Send reminders to a selected set of accounts."""
    if request.method == 'POST':
        account_ids = request.POST.getlist('accounts')
        reminder_type = request.POST.get('reminder_type')
        if not account_ids or not reminder_type:
            messages.error(request, 'Select at least one account and a reminder type.')
            return redirect('school_fees_send_reminders')

        accounts = scope_queryset(
            StudentFeeAccount.objects.filter(pk__in=account_ids), request)
        sent = reminder_service.send_reminders_for_accounts(
            accounts, reminder_type, sent_by=request.user)
        success = sum(1 for s in sent if s.status == ReminderLog.Status.SENT)
        messages.success(request, f'Sent {success} reminder(s).')
        return redirect('school_fees_reminder_log')

    accounts = scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request)
    accounts = [a for a in accounts if a.outstanding_balance > 0]
    context = {
        'accounts': accounts,
        'reminder_types': ReminderTemplate.ReminderType.choices,
        'templates': ReminderTemplate.objects.all(),
    }
    return render(request, 'school_fees/send_reminders.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def send_reminders_by_class(request):
    if request.method == 'POST':
        class_pk = request.POST.get('school_class')
        term_pk = request.POST.get('term')
        reminder_type = request.POST.get('reminder_type')
        if not class_pk or not reminder_type:
            messages.error(request, 'Select a class and reminder type.')
            return redirect('school_fees_send_reminders_by_class')

        accounts = scope_queryset(StudentFeeAccount.objects.filter(
            student__school_class_id=class_pk), request)
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
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
        'terms': scope_reference_queryset(Term.objects.select_related('academic_year'), request,
                                field='academic_year__school'),
        'reminder_types': ReminderTemplate.ReminderType.choices,
    }
    return render(request, 'school_fees/send_reminders_class.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def send_overdue_reminders(request):
    """Send overdue reminders to all overdue accounts."""
    if request.method == 'POST':
        accounts = [a for a in scope_queryset(StudentFeeAccount.objects.all(), request) if a.is_overdue]
        if not accounts:
            messages.error(request, 'No overdue accounts found.')
            return redirect('school_fees_fee_account_list')
        sent = reminder_service.send_reminders_for_accounts(
            accounts, ReminderTemplate.ReminderType.OVERDUE, sent_by=request.user)
        success = sum(1 for s in sent if s.status == ReminderLog.Status.SENT)
        messages.success(request, f'Sent {success} overdue reminder(s).')
        return redirect('school_fees_reminder_log')
    overdue_accounts = [a for a in scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class'), request) if a.is_overdue]
    return render(request, 'school_fees/send_overdue_reminders.html',
                  {'overdue_accounts': overdue_accounts})


@login_required
@role_required(*STAFF_ROLES)
def reminder_log(request):
    logs = scope_queryset(ReminderLog.objects.select_related('student', 'sent_by'), request)
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
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def reports(request):
    return render(request, 'school_fees/reports.html',
                  {'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
                   'status_choices': StudentFeeAccount.PaymentStatus.choices})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_daily_payments(request):
    date = request.GET.get('date') or timezone.now().date()
    payments = scope_queryset(FeePayment.objects.filter(payment_date=date).select_related(
        'student', 'student__school_class', 'recorded_by'), request)
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'date': date,
               'title': 'Daily Payments'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_monthly_payments(request):
    month = request.GET.get('month') or timezone.now().strftime('%Y-%m')
    payments = scope_queryset(FeePayment.objects.filter(
        payment_date__startswith=month).select_related(
        'student', 'student__school_class', 'recorded_by'), request)
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'month': month,
               'title': 'Monthly Payments'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_year_collections(request):
    year_pk = request.GET.get('academic_year')
    year = get_object_or_404(
        scope_reference_queryset(AcademicYear.objects.all(), request), pk=year_pk) if year_pk else None
    payments = scope_queryset(FeePayment.objects.all(), request)
    if year:
        payments = payments.filter(academic_year=year)
    payments = payments.select_related('student', 'student__school_class', 'term')
    total = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    context = {'payments': payments, 'total': total, 'year': year,
               'academic_years': scope_reference_queryset(AcademicYear.objects.all(), request),
               'title': 'Academic Year Collections'}
    return render(request, 'school_fees/report_payments.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_outstanding(request):
    accounts = [a for a in scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request) if a.outstanding_balance > 0]
    total = sum(a.outstanding_balance for a in accounts)
    context = {'accounts': accounts, 'total': total, 'title': 'Outstanding Fees'}
    return render(request, 'school_fees/report_outstanding.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_overdue(request):
    accounts = [a for a in scope_queryset(StudentFeeAccount.objects.select_related(
        'student', 'student__school_class', 'academic_year', 'term'), request) if a.is_overdue]
    total = sum(a.outstanding_balance for a in accounts)
    context = {'accounts': accounts, 'total': total, 'title': 'Overdue Fees'}
    return render(request, 'school_fees/report_outstanding.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_status(request):
    status = request.GET.get('status', 'FULLY_PAID')
    if status == 'OVERDUE':
        accounts = [a for a in scope_queryset(StudentFeeAccount.objects.select_related(
            'student', 'student__school_class', 'academic_year', 'term'), request) if a.is_overdue]
    else:
        accounts = scope_queryset(
            StudentFeeAccount.objects.filter(status=status).select_related(
                'student', 'student__school_class', 'academic_year', 'term'), request)
    context = {'accounts': accounts, 'status': status,
               'status_choices': StudentFeeAccount.PaymentStatus.choices,
               'title': f'{status.replace("_", " ").title()} Students'}
    return render(request, 'school_fees/report_status.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def report_class_collections(request):
    class_pk = request.GET.get('school_class')
    scoped_classes = scope_queryset(SchoolClass.objects.all(), request)
    scoped_accounts = scope_queryset(StudentFeeAccount.objects.all(), request)
    if class_pk:
        scoped_classes = scoped_classes.filter(pk=class_pk)
    reports = []
    for sc in scoped_classes:
        row = {
            'school_class': sc,
            'expected': Decimal('0.00'),
            'collected': Decimal('0.00'),
            'outstanding': Decimal('0.00'),
        }
        accounts = scoped_accounts.filter(student__school_class=sc)
        row['expected'] = accounts.aggregate(total=Sum('total_fees'))['total'] or Decimal('0.00')
        row['collected'] = accounts.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
        row['outstanding'] = row['expected'] - row['collected']
        reports.append(row)
    total_expected = sum(r['expected'] for r in reports)
    total_collected = sum(r['collected'] for r in reports)
    total_outstanding = sum(r['outstanding'] for r in reports)
    context = {
        'reports': reports,
        'school_classes': scope_queryset(SchoolClass.objects.all(), request),
        'selected_class': class_pk,
        'total_expected': total_expected,
        'total_collected': total_collected,
        'total_outstanding': total_outstanding,
        'title': 'Class/Program Fee Collections',
    }
    return render(request, 'school_fees/report_class_collections.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'HEAD_TEACHER')
def export_fees_csv(request):
    report_type = request.GET.get('type', 'payments')
    response = HttpResponse(content_type='text/csv')
    if report_type == 'payments':
        response['Content-Disposition'] = 'attachment; filename="fee_payments.csv"'
        writer = csv.writer(response)
        writer.writerow(['School', 'Receipt #', 'Student ID', 'Student', 'Class', 'Amount',
                         'Method', 'Reference', 'Date', 'Recorded By'])
        for p in scope_queryset(FeePayment.objects.select_related(
                'student', 'student__school_class', 'recorded_by'), request):
            writer.writerow([
                (p.school.name if p.school else ''), p.receipt_number, p.student.student_id,
                p.student.get_full_name(),
                p.student.school_class.name, p.amount, p.get_payment_method_display(),
                p.reference, p.payment_date, (p.recorded_by.get_full_name() if p.recorded_by else ''),
            ])
    elif report_type == 'accounts':
        response['Content-Disposition'] = 'attachment; filename="fee_accounts.csv"'
        writer = csv.writer(response)
        writer.writerow(['School', 'Account #', 'Student ID', 'Student', 'Class', 'Year', 'Term',
                         'Total Fees', 'Amount Paid', 'Outstanding', 'Status'])
        for a in scope_queryset(StudentFeeAccount.objects.select_related(
                'student', 'student__school_class', 'academic_year', 'term'), request):
            writer.writerow([
                (a.school.name if a.school else ''), a.account_number, a.student.student_id,
                a.student.get_full_name(),
                a.student.school_class.name, a.academic_year.name, a.term.name,
                a.total_fees, a.amount_paid, a.outstanding_balance, a.status,
            ])
    elif report_type == 'outstanding':
        response['Content-Disposition'] = 'attachment; filename="outstanding_fees.csv"'
        writer = csv.writer(response)
        writer.writerow(['School', 'Account #', 'Student ID', 'Student', 'Class', 'Year', 'Term',
                         'Total Fees', 'Amount Paid', 'Outstanding'])
        for a in scope_queryset(StudentFeeAccount.objects.select_related(
                'student', 'student__school_class', 'academic_year', 'term'), request):
            if a.outstanding_balance > 0:
                writer.writerow([
                    (a.school.name if a.school else ''), a.account_number, a.student.student_id,
                    a.student.get_full_name(),
                    a.student.school_class.name, a.academic_year.name, a.term.name,
                    a.total_fees, a.amount_paid, a.outstanding_balance,
                ])
    return response
