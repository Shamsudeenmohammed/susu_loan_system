from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from django.db.models import Q
from apps.core.decorators import role_required
from .models import Customer
from .forms import CustomerForm, CustomerSearchForm


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER', 'COLLECTOR')
def customer_list(request):
    form = CustomerSearchForm(request.GET)
    customers = Customer.objects.all()

    if form.is_valid():
        query = form.cleaned_data.get('query')
        status = form.cleaned_data.get('status')

        if query:
            customers = customers.filter(
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(phone__icontains=query) |
                Q(customer_number__icontains=query) |
                Q(email__icontains=query)
            )
        if status:
            customers = customers.filter(status=status)

    context = {
        'customers': customers.select_related('registered_by'),
        'form': form,
        'total_count': customers.count(),
    }
    return render(request, 'customers/customer_list.html', context)


class CustomerCreateView(LoginRequiredMixin, View):
    def get(self, request):
        if not request.user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER'):
            messages.error(request, 'You do not have permission to create customers.')
            return redirect('customer_list')
        form = CustomerForm()
        return render(request, 'customers/customer_form.html', {'form': form, 'title': 'Register New Customer'})

    def post(self, request):
        if not request.user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CASHIER'):
            messages.error(request, 'You do not have permission to create customers.')
            return redirect('customer_list')
        form = CustomerForm(request.POST, request.FILES)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.registered_by = request.user
            customer.save()
            messages.success(request, f'Customer {customer.customer_number} created successfully.')
            return redirect('customer_detail', pk=customer.pk)
        return render(request, 'customers/customer_form.html', {'form': form, 'title': 'Register New Customer'})


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)

    if request.user.has_role('CUSTOMER'):
        if not request.user.customer_profile or request.user.customer_profile.pk != pk:
            messages.error(request, 'You do not have access to this customer.')
            return redirect('customer_dashboard')

    from apps.susu.models import SusuAccount
    susu_accounts = SusuAccount.objects.filter(customer=customer)

    context = {
        'customer': customer,
        'susu_accounts': susu_accounts,
    }
    return render(request, 'customers/customer_detail.html', context)


class CustomerUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        if request.user.has_role('CUSTOMER'):
            if not request.user.customer_profile or request.user.customer_profile.pk != pk:
                messages.error(request, 'Access denied.')
                return redirect('customer_dashboard')
        if not request.user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER') and not request.user.has_role('CUSTOMER'):
            messages.error(request, 'You do not have permission.')
            return redirect('customer_list')
        form = CustomerForm(instance=customer)
        return render(request, 'customers/customer_form.html', {'form': form, 'title': f'Edit {customer.customer_number}', 'customer': customer})

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        if not request.user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER') and not request.user.has_role('CUSTOMER'):
            messages.error(request, 'You do not have permission.')
            return redirect('customer_list')
        form = CustomerForm(request.POST, request.FILES, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, 'Customer updated successfully.')
            return redirect('customer_detail', pk=pk)
        return render(request, 'customers/customer_form.html', {'form': form, 'title': f'Edit {customer.customer_number}', 'customer': customer})
