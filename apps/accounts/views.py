from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from .forms import LoginForm, CustomerRegistrationForm
from .models import User
from apps.customers.models import Customer
from apps.audit.models import AuditLog
from apps.notifications.services import messages as message_templates
from apps.notifications.services.sms import get_sms_service


class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return self._redirect_by_role(request)
        form = LoginForm()
        return render(request, 'registration/login.html', {'form': form})

    def post(self, request):
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user.has_role('CUSTOMER') and hasattr(user, 'customer_profile'):
                customer = user.customer_profile
                if customer.is_pending:
                    messages.error(
                        request,
                        'Your account is awaiting administrator approval. '
                        'You will be able to log in once it has been approved.',
                    )
                    return render(request, 'registration/login.html', {'form': form})
                if customer.is_rejected:
                    messages.error(
                        request,
                        'Your account registration was rejected. Please contact support.',
                    )
                    return render(request, 'registration/login.html', {'form': form})
            login(request, user)
            messages.success(request, f'Welcome back, {user.first_name or user.email}!')
            return self._redirect_by_role(request)
        return render(request, 'registration/login.html', {'form': form})

    def _redirect_by_role(self, request):
        user = request.user
        if user.has_role('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'LOAN_OFFICER', 'CASHIER', 'COLLECTOR'):
            return redirect('dashboard')
        if user.has_role('CUSTOMER'):
            if not hasattr(user, 'customer_profile'):
                logout(request)
                messages.error(request, 'Your account has no customer profile. Please contact support.')
                return redirect('login')
            return redirect('customer_dashboard')
        return redirect('login')


@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('login')


class RegisterView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        form = CustomerRegistrationForm()
        return render(request, 'registration/register.html', {'form': form})

    def post(self, request):
        form = CustomerRegistrationForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, 'registration/register.html', {'form': form})

        data = form.cleaned_data

        user = User(
            email=data['email'],
            first_name=data['first_name'],
            last_name=data['last_name'],
            phone=data['phone'],
            role='CUSTOMER',
            is_active=True,
        )
        user.set_password(data['password1'])
        user.save()

        customer = Customer.objects.create(
            user=user,
            first_name=data['first_name'],
            last_name=data['last_name'],
            date_of_birth=data.get('date_of_birth'),
            gender=data.get('gender') or '',
            phone=data['phone'],
            alt_phone=data.get('alt_phone') or '',
            email=data['email'],
            address=data.get('address') or '',
            occupation=data.get('occupation') or '',
            emergency_contact_name=data.get('emergency_contact_name') or '',
            emergency_contact_phone=data.get('emergency_contact_phone') or '',
            id_type=data.get('id_type') or '',
            id_number=data.get('id_number') or '',
            photo=data.get('photo'),
            status=Customer.Status.PENDING,
        )

        AuditLog.log(
            action=AuditLog.ActionType.CUSTOMER_CREATED,
            description=(
                f"Customer {customer.customer_number} ({customer.get_full_name()}) "
                f"registered and is pending approval."
            ),
            user=user,
            object_type='Customer',
            object_id=customer.pk,
            ip_address=_client_ip(request),
        )

        _send_registration_ack(customer)

        messages.success(
            request,
            f'Account created successfully! Your customer ID is {customer.customer_number}. '
            'Your account is pending approval by our team. You will be notified once approved.',
        )
        return redirect('login')


def _client_ip(request):
    ip = request.META.get('HTTP_X_FORWARDED_FOR')
    if ip:
        return ip.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _send_registration_ack(customer):
    """Best-effort SMS informing the customer their account is pending approval."""
    phone = customer.phone
    if not phone:
        return
    try:
        get_sms_service().send_sms(
            phone_number=phone,
            message=message_templates.account_created(customer.get_full_name()),
            notification_type='CUSTOMER_CREATED',
            customer=customer,
            reference_model='Customer',
            reference_id=customer.pk,
            unique_key=f'customer_created:{customer.pk}',
        )
    except Exception:  # pragma: no cover - never block registration on SMS
        pass
