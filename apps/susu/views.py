from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from apps.core.decorators import role_required
from .models import SusuAccount
from .forms import SusuAccountForm


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER')
def susu_account_list(request):
    accounts = SusuAccount.objects.select_related('customer', 'opened_by').all()
    context = {
        'accounts': accounts,
        'total_count': accounts.count(),
    }
    return render(request, 'susu/susu_account_list.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER')
def susu_account_create(request):
    if request.method == 'POST':
        form = SusuAccountForm(request.POST)
        if form.is_valid():
            account = form.save(commit=False)
            account.opened_by = request.user
            account.save()
            messages.success(request, f'Susu account {account.account_number} created successfully.')
            return redirect('susu_account_detail', pk=account.pk)
    else:
        form = SusuAccountForm()
    return render(request, 'susu/susu_account_form.html', {'form': form, 'title': 'Open New Susu Account'})


@login_required
def susu_account_detail(request, pk):
    account = get_object_or_404(
        SusuAccount.objects.select_related('customer', 'opened_by'),
        pk=pk
    )

    if request.user.has_role('CUSTOMER'):
        if not request.user.customer_profile or request.user.customer_profile.pk != account.customer.pk:
            messages.error(request, 'Access denied.')
            return redirect('customer_dashboard')

    from apps.payments.models import Transaction
    transactions = Transaction.objects.filter(account=account).order_by('-created_at')[:20]

    context = {
        'account': account,
        'transactions': transactions,
    }
    return render(request, 'susu/susu_account_detail.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def susu_account_update(request, pk):
    account = get_object_or_404(SusuAccount, pk=pk)
    if request.method == 'POST':
        form = SusuAccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            messages.success(request, 'Account updated successfully.')
            return redirect('susu_account_detail', pk=pk)
    else:
        form = SusuAccountForm(instance=account)
    return render(request, 'susu/susu_account_form.html', {'form': form, 'title': f'Edit {account.account_number}'})
