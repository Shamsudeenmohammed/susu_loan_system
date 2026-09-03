from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from django.views.decorators.http import require_POST
from django.db.models import Q
from apps.core.decorators import role_required
from .models import Customer
from .forms import CustomerForm, CustomerSearchForm
from .services import approve_customer, reject_customer


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
        'can_manage': request.user.has_role('SUPER_ADMIN', 'ADMIN'),
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
    inactive_susu_accounts = susu_accounts.filter(status=SusuAccount.Status.INACTIVE)

    context = {
        'customer': customer,
        'susu_accounts': susu_accounts,
        'inactive_susu_accounts': inactive_susu_accounts,
        'can_approve': request.user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER'),
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


def _client_ip(request):
    ip = request.META.get("HTTP_X_FORWARDED_FOR")
    if ip:
        return ip.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@login_required
@role_required("SUPER_ADMIN", "ADMIN", "MANAGER")
def pending_customers(request):
    """Shows customers awaiting approval."""
    pending = Customer.objects.filter(status=Customer.Status.PENDING).select_related("registered_by")
    context = {
        "pending_customers": pending,
        "total_pending": pending.count(),
        "total_all": Customer.objects.count(),
        "total_approved": Customer.objects.filter(status=Customer.Status.ACTIVE).count(),
    }
    return render(request, "customers/pending_approvals.html", context)


@login_required
@require_POST
@role_required("SUPER_ADMIN", "ADMIN", "MANAGER")
def customer_approve(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    result = approve_customer(customer, actor=request.user, ip_address=_client_ip(request))
    if result.changed:
        if result.sms_sent:
            messages.success(request, f"Customer Approved � {result.sms_message}")
        else:
            messages.warning(request, f"Customer Approved � {result.sms_message}")
    else:
        messages.info(request, result.sms_message)
    return redirect("customer_detail", pk=customer.pk)


@login_required
@require_POST
@role_required("SUPER_ADMIN", "ADMIN", "MANAGER")
def customer_reject(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    reason = request.POST.get("reason", "").strip()
    result = reject_customer(customer, actor=request.user, ip_address=_client_ip(request), reason=reason)
    if result.changed:
        if result.sms_sent:
            messages.warning(request, f"Customer Rejected � {result.sms_message}")
        else:
            messages.warning(request, result.sms_message)
    else:
        messages.info(request, result.sms_message)
    return redirect("customer_detail", pk=customer.pk)


@login_required
@require_POST
@role_required("SUPER_ADMIN", "ADMIN", "MANAGER")
def susu_account_activate(request, pk):
    from apps.susu.models import SusuAccount
    from apps.susu.services import activate_susu_account
    account = get_object_or_404(SusuAccount, pk=pk)
    result = activate_susu_account(account, actor=request.user, ip_address=_client_ip(request))
    if result.changed:
        if result.sms_sent:
            messages.success(request, f"Susu Account Activated - {result.sms_message}")
        else:
            messages.warning(request, result.sms_message)
    else:
        messages.info(request, result.sms_message)
    return redirect("susu_account_detail", pk=account.pk)


@login_required
@require_POST
@role_required("SUPER_ADMIN", "ADMIN")
def customer_toggle_status(request, pk):
    customer = get_object_or_404(Customer, pk=pk)

    if customer.status == Customer.Status.ACTIVE:
        customer.status = Customer.Status.INACTIVE
        customer.save(update_fields=['status', 'updated_at'])
        messages.warning(request, f"{customer.get_full_name()} has been deactivated.")
    elif customer.status == Customer.Status.INACTIVE:
        customer.status = Customer.Status.ACTIVE
        customer.save(update_fields=['status', 'updated_at'])
        messages.success(request, f"{customer.get_full_name()} has been activated.")
    else:
        messages.info(request, f"Cannot toggle status for a customer with status: {customer.status}.")
        return redirect("customer_list")

    AuditLog = _get_audit_model()
    if AuditLog:
        AuditLog.log(
            action="CUSTOMER_STATUS_TOGGLE",
            description=f"Status changed to {customer.status} by {request.user.username}",
            user=request.user,
            object_type="Customer",
            object_id=str(customer.pk),
            ip_address=_client_ip(request),
        )

    return redirect("customer_list")


@login_required
@require_POST
@role_required("SUPER_ADMIN", "ADMIN")
def customer_delete(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    customer_name = customer.get_full_name()
    customer_number = customer.customer_number

    # A customer with financial/loan history cannot be deleted outright — doing
    # so would destroy protected financial records and break integrity. Refuse
    # the hard delete and point the user to deactivation instead.
    protected_related = ['loans', 'eligibility_audits', 'transactions', 'withdrawals']
    blocked_by = []
    for rel in protected_related:
        manager = getattr(customer, rel, None)
        if manager is not None and manager.exists():
            blocked_by.append(rel)

    if blocked_by:
        detail = ", ".join(rel.replace('_', ' ') for rel in blocked_by)
        messages.error(
            request,
            f"Customer {customer_number} ({customer_name}) cannot be deleted because they have "
            f"financial records ({detail}). Deactivate the customer instead to disable their account.",
        )
        return redirect("customer_list")

    AuditLog = _get_audit_model()
    if AuditLog:
        AuditLog.log(
            action="CUSTOMER_DELETED",
            description=f"Customer {customer_number} ({customer_name}) deleted by {request.user.username}",
            user=request.user,
            object_type="Customer",
            object_id=str(customer.pk),
            ip_address=_client_ip(request),
        )

    customer.delete()
    messages.success(request, f"Customer {customer_number} ({customer_name}) has been deleted.")
    return redirect("customer_list")


def _get_audit_model():
    try:
        from apps.core.models import AuditLog
        return AuditLog
    except ImportError:
        return None
