from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction as db_transaction
from django.utils import timezone
from decimal import Decimal
from apps.core.decorators import role_required
from .models import Loan, LoanProduct, RepaymentSchedule, LoanRepayment, LoanPolicy, EligibilityAudit
from .forms import LoanProductForm, LoanApplicationForm, LoanReviewForm, RepaymentForm, LoanPolicyForm
from .services import calculate_repayment_schedule, apply_repayment


# --- Loan Products ---

@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_product_list(request):
    products = LoanProduct.objects.all()
    return render(request, 'loans/loan_product_list.html', {'products': products})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_product_create(request):
    if request.method == 'POST':
        form = LoanProductForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Loan product created.')
            return redirect('loan_product_list')
    else:
        form = LoanProductForm()
    return render(request, 'loans/loan_product_form.html', {'form': form, 'title': 'Create Loan Product'})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_product_update(request, pk):
    product = get_object_or_404(LoanProduct, pk=pk)
    if request.method == 'POST':
        form = LoanProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'Loan product updated.')
            return redirect('loan_product_list')
    else:
        form = LoanProductForm(instance=product)
    return render(request, 'loans/loan_product_form.html', {'form': form, 'title': f'Edit {product.name}'})


# --- Loan Applications ---

@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'LOAN_OFFICER')
def loan_list(request):
    loans = Loan.objects.select_related('customer', 'loan_product', 'approved_by').all()
    status = request.GET.get('status')
    if status:
        loans = loans.filter(status=status)
    return render(request, 'loans/loan_list.html', {'loans': loans})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'LOAN_OFFICER', 'CUSTOMER')
def loan_apply(request):
    customer = None
    if request.user.has_role('CUSTOMER'):
        if not hasattr(request.user, 'customer_profile'):
            messages.error(request, 'No customer profile found.')
            return redirect('customer_dashboard')
        customer = request.user.customer_profile

        from .eligibility import LoanEligibilityService
        result, audit = LoanEligibilityService.check_eligibility(customer)

        if not result.eligible:
            messages.warning(request, 'You are not currently eligible for a loan. Please review your eligibility status.')
            return redirect('loan_eligibility')

    if request.method == 'POST':
        form = LoanApplicationForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            loan_customer = cd['customer'] if not customer else customer

            if request.user.has_role('CUSTOMER'):
                from .eligibility import LoanEligibilityService
                result, audit = LoanEligibilityService.check_eligibility(
                    loan_customer, requested_amount=cd['principal_amount']
                )
                if not result.eligible:
                    for reason in result.reasons:
                        messages.error(request, reason)
                    return redirect('loan_eligibility')

            loan = Loan(
                customer=loan_customer,
                loan_product=cd['loan_product'],
                principal_amount=cd['principal_amount'],
                term_months=cd['term_months'],
                purpose=cd.get('purpose', ''),
                income_info=cd.get('income_info', ''),
                status='SUBMITTED',
                submitted_by=request.user,
            )
            loan.calculate_financials()
            loan.outstanding_balance = loan.total_amount

            if request.user.has_role('CUSTOMER'):
                from .eligibility import LoanEligibilityService
                _, audit = LoanEligibilityService.check_eligibility(loan_customer)
                loan.eligibility_snapshot = audit.to_snapshot()

            loan.save()

            messages.success(request, f'Loan application {loan.loan_number} submitted.')

            from apps.notifications.tasks import send_loan_application_sms
            try:
                send_loan_application_sms.delay(loan.pk)
            except Exception:
                pass

            if request.user.has_role('CUSTOMER'):
                return redirect('customer_dashboard')
            return redirect('loan_detail', pk=loan.pk)
    else:
        initial = {}
        if customer:
            initial['customer'] = customer
        form = LoanApplicationForm(initial=initial)
        if customer:
            form.fields['customer'].initial = customer.pk
            form.fields['customer'].widget = forms.HiddenInput()
    return render(request, 'loans/loan_apply.html', {'form': form})


@login_required
def loan_detail(request, pk):
    loan = get_object_or_404(
        Loan.objects.select_related('customer', 'loan_product', 'approved_by', 'submitted_by'),
        pk=pk
    )
    if request.user.has_role('CUSTOMER'):
        if not request.user.customer_profile or request.user.customer_profile.pk != loan.customer.pk:
            messages.error(request, 'Access denied.')
            return redirect('customer_dashboard')

    schedules = loan.repayment_schedules.all()
    repayments = loan.repayments.all()

    context = {
        'loan': loan,
        'schedules': schedules,
        'repayments': repayments,
    }
    return render(request, 'loans/loan_detail.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_review(request, pk):
    loan = get_object_or_404(Loan, pk=pk)
    if loan.status not in ['SUBMITTED', 'UNDER_REVIEW']:
        messages.error(request, 'This loan cannot be reviewed.')
        return redirect('loan_detail', pk=pk)

    if request.method == 'POST':
        form = LoanReviewForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data['decision']
            notes = form.cleaned_data['notes']

            if decision == 'APPROVE':
                loan.status = Loan.Status.APPROVED
                loan.approval_date = timezone.now()
                loan.approved_by = request.user
                loan.save()
                messages.success(request, f'Loan {loan.loan_number} approved.')
            else:
                loan.status = Loan.Status.REJECTED
                loan.rejected_by = request.user
                loan.rejection_reason = notes
                loan.save()
                messages.info(request, f'Loan {loan.loan_number} rejected.')
            return redirect('loan_detail', pk=pk)
    else:
        form = LoanReviewForm()
    return render(request, 'loans/loan_review.html', {'form': form, 'loan': loan})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def loan_disburse(request, pk):
    loan = get_object_or_404(Loan, pk=pk)
    if loan.status != Loan.Status.APPROVED:
        messages.error(request, 'Only approved loans can be disbursed.')
        return redirect('loan_detail', pk=pk)

    if request.method == 'POST':
        from apps.payments.models import Transaction
        from apps.susu.models import SusuAccount

        with db_transaction.atomic():
            susu_account = SusuAccount.objects.filter(customer=loan.customer, status='ACTIVE').first()

            balance_before = susu_account.current_balance if susu_account else Decimal('0.00')
            balance_after = balance_before + loan.disbursement_amount

            txn = Transaction.objects.create(
                customer=loan.customer,
                account=susu_account,
                transaction_type=Transaction.TransactionType.LOAN_DISBURSEMENT,
                amount=loan.disbursement_amount,
                balance_before=balance_before,
                balance_after=balance_after,
                reference=loan.loan_number,
                description=f'Loan disbursement for {loan.loan_number}',
                created_by=request.user,
            )

            if susu_account:
                susu_account.current_balance = balance_after
                susu_account.save(update_fields=['current_balance'])

            loan.disbursement_date = timezone.now()
            loan.status = Loan.Status.DISBURSED
            loan.transaction = txn
            loan.save()

            schedule = calculate_repayment_schedule(loan)
            for item in schedule:
                RepaymentSchedule.objects.create(
                    loan=loan,
                    installment_number=item['installment_number'],
                    due_date=item['due_date'],
                    principal_due=item['principal_due'],
                    interest_due=item['interest_due'],
                    total_due=item['total_due'],
                    remaining_balance=item['remaining_balance'],
                )

            loan.status = Loan.Status.ACTIVE
            loan.maturity_date = schedule[-1]['due_date'] if schedule else None
            loan.save()

        messages.success(request, f'Loan {loan.loan_number} disbursed and repayment schedule generated.')

        from apps.notifications.tasks import send_loan_disbursement_sms
        try:
            send_loan_disbursement_sms.delay(loan.pk)
        except Exception:
            pass

        return redirect('loan_detail', pk=pk)

    return render(request, 'loans/loan_disburse.html', {'loan': loan})


# --- Repayments ---

@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'LOAN_OFFICER', 'CASHIER')
def record_repayment(request):
    if request.method == 'POST':
        form = RepaymentForm(request.POST)
        if form.is_valid():
            loan = form.cleaned_data['loan']
            amount = form.cleaned_data['amount']
            payment_method = form.cleaned_data['payment_method']
            reference = form.cleaned_data['reference']
            notes = form.cleaned_data['notes']

            with db_transaction.atomic():
                # Find first unpaid installment
                installment = loan.repayment_schedules.filter(
                    status__in=['PENDING', 'PARTIALLY_PAID']
                ).order_by('due_date').first()

                if not installment:
                    messages.error(request, 'No pending installments for this loan.')
                    return redirect('loan_detail', pk=loan.pk)

                installment, excess = apply_repayment(loan, installment, amount)

                loan.outstanding_balance = loan.outstanding_balance - amount
                if loan.outstanding_balance <= 0:
                    loan.outstanding_balance = Decimal('0.00')
                    loan.status = Loan.Status.COMPLETED

                from apps.payments.models import Transaction
                from apps.susu.models import SusuAccount
                susu_account = SusuAccount.objects.filter(customer=loan.customer, status='ACTIVE').first()
                balance_before = susu_account.current_balance if susu_account else Decimal('0.00')
                balance_after = balance_before - amount

                txn = Transaction.objects.create(
                    customer=loan.customer,
                    account=susu_account,
                    transaction_type=Transaction.TransactionType.LOAN_REPAYMENT,
                    amount=amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    payment_method=payment_method,
                    reference=reference or loan.loan_number,
                    description=notes or f'Loan repayment for {loan.loan_number}',
                    created_by=request.user,
                )

                if susu_account:
                    susu_account.current_balance = balance_after
                    susu_account.save(update_fields=['current_balance'])

                loan.transaction = txn
                loan.save()

                repayment = LoanRepayment.objects.create(
                    loan=loan,
                    installment=installment,
                    amount=amount,
                    payment_method=payment_method,
                    reference=reference,
                    notes=notes,
                    recorded_by=request.user,
                    transaction=txn,
                )

            messages.success(request, f'Repayment of GHS {amount:.2f} recorded. {repayment.repayment_number}')

            from apps.notifications.tasks import send_repayment_sms
            try:
                send_repayment_sms.delay(repayment.pk)
            except Exception:
                pass

            return redirect('loan_detail', pk=loan.pk)
    else:
        form = RepaymentForm()
    return render(request, 'loans/repayment_form.html', {'form': form})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'LOAN_OFFICER', 'CASHIER')
def loan_repayment_list(request):
    repayments = LoanRepayment.objects.select_related('loan', 'loan__customer', 'recorded_by').all()
    return render(request, 'loans/repayment_list.html', {'repayments': repayments})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def overdue_loans(request):
    today = timezone.now().date()
    overdue = RepaymentSchedule.objects.filter(
        due_date__lt=today,
        status__in=['PENDING', 'PARTIALLY_PAID']
    ).select_related('loan', 'loan__customer')

    # Mark as overdue
    overdue.update(status='Overdue')

    return render(request, 'loans/overdue_loans.html', {'overdue': overdue})


# --- Loan Eligibility ---

@login_required
def loan_eligibility(request):
    if request.user.has_role('CUSTOMER'):
        if not hasattr(request.user, 'customer_profile'):
            messages.error(request, 'No customer profile found.')
            return redirect('customer_dashboard')
        customer = request.user.customer_profile
    else:
        customer_id = request.GET.get('customer_id')
        if customer_id:
            from apps.customers.models import Customer
            customer = get_object_or_404(Customer, pk=customer_id)
        else:
            messages.info(request, 'Select a customer to view eligibility.')
            return redirect('customer_list')

    from .eligibility import LoanEligibilityService
    result, audit = LoanEligibilityService.check_eligibility(customer)

    from apps.susu.models import SusuAccount
    susu_accounts = SusuAccount.objects.filter(customer=customer, status='ACTIVE')
    products = LoanProduct.objects.filter(is_active=True)

    context = {
        'customer': customer,
        'result': result,
        'audit': audit,
        'susu_accounts': susu_accounts,
        'products': products,
    }
    return render(request, 'loans/loan_eligibility.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN')
def loan_policy_list(request):
    policies = LoanPolicy.objects.all()
    return render(request, 'loans/loan_policy_list.html', {'policies': policies})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN')
def loan_policy_create(request):
    if request.method == 'POST':
        form = LoanPolicyForm(request.POST)
        if form.is_valid():
            policy = form.save()
            messages.success(request, f'Loan policy "{policy.name}" created.')
            return redirect('loan_policy_list')
    else:
        form = LoanPolicyForm()
    return render(request, 'loans/loan_policy_form.html', {'form': form, 'title': 'Create Loan Policy'})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN')
def loan_policy_update(request, pk):
    policy = get_object_or_404(LoanPolicy, pk=pk)
    if request.method == 'POST':
        form = LoanPolicyForm(request.POST, instance=policy)
        if form.is_valid():
            form.save()
            messages.success(request, f'Loan policy "{policy.name}" updated.')
            return redirect('loan_policy_list')
    else:
        form = LoanPolicyForm(instance=policy)
    return render(request, 'loans/loan_policy_form.html', {'form': form, 'title': f'Edit {policy.name}'})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN')
def eligibility_audit_list(request):
    audits = EligibilityAudit.objects.select_related('customer', 'policy').all()
    customer_id = request.GET.get('customer_id')
    if customer_id:
        audits = audits.filter(customer_id=customer_id)
    return render(request, 'loans/eligibility_audit_list.html', {'audits': audits[:100]})
