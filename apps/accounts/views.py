from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.views.decorators.http import require_POST
from django.utils import timezone
from .forms import (
    LoginForm, CustomerRegistrationForm, StaffCreationForm,
    ForgotPasswordForm, OTPVerificationForm, PasswordResetForm
)
from .models import User, PasswordResetOTP
from apps.customers.models import Customer
from apps.audit.models import AuditLog
from apps.notifications.services import messages as message_templates
from apps.notifications.services.sms import get_sms_service
from apps.core.utils import normalize_ghana_phone


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
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
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
            username=data['username'],
            email=data.get('email') or None,
            first_name=data['first_name'],
            last_name=data['last_name'],
            phone=normalize_ghana_phone(data['phone']),
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
            phone=normalize_ghana_phone(data['phone']),
            email=data.get('email') or '',
            address=data.get('address') or '',
            occupation=data.get('occupation') or '',
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
            'Your account is pending approval by our team. You will be notified once approved.'
        )
        return redirect('login')


class ForgotPasswordView(View):
    """Step 1: Request password reset by entering registered phone number."""
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard')
        form = ForgotPasswordForm()
        return render(request, 'registration/forgot_password.html', {'form': form})

    def post(self, request):
        form = ForgotPasswordForm(request.POST)
        if not form.is_valid():
            return render(request, 'registration/forgot_password.html', {'form': form})

        phone = form.cleaned_data['phone']

        # Generate OTP
        otp_obj = PasswordResetOTP.generate_otp(phone)

        # Send OTP via SMS
        try:
            customer = Customer.objects.get(phone=phone)
            get_sms_service().send_sms(
                phone_number=phone,
                message=message_templates.otp_verification(otp_obj.otp_code),
                notification_type='PASSWORD_RESET_OTP',
                customer=customer,
                reference_model='PasswordResetOTP',
                reference_id=otp_obj.pk,
                unique_key=f'password_reset_otp:{otp_obj.pk}',
            )
            messages.success(request, 'A verification code has been sent to your registered phone number.')
        except Exception:
            messages.error(request, 'Failed to send verification code. Please try again.')
            return render(request, 'registration/forgot_password.html', {'form': form})

        # Store phone in session for next step
        request.session['password_reset_phone'] = phone
        return redirect('verify_otp')


class VerifyOTPView(View):
    """Step 2: Verify the OTP sent via SMS."""
    def get(self, request):
        phone = request.session.get('password_reset_phone')
        if not phone:
            messages.error(request, 'Session expired. Please start over.')
            return redirect('forgot_password')

        form = OTPVerificationForm(phone=phone)
        return render(request, 'registration/verify_otp.html', {'form': form, 'phone': phone})

    def post(self, request):
        phone = request.session.get('password_reset_phone')
        if not phone:
            messages.error(request, 'Session expired. Please start over.')
            return redirect('forgot_password')

        form = OTPVerificationForm(request.POST, phone=phone)
        if not form.is_valid():
            return render(request, 'registration/verify_otp.html', {'form': form, 'phone': phone})

        otp_code = form.cleaned_data['otp']

        # Find the latest unused OTP for this phone
        try:
            otp_obj = PasswordResetOTP.objects.filter(
                phone=phone, is_used=False
            ).latest('created_at')
        except PasswordResetOTP.DoesNotExist:
            messages.error(request, 'No valid verification code found. Please request a new one.')
            return redirect('forgot_password')

        # Verify OTP
        is_valid, message = otp_obj.verify(otp_code)
        if not is_valid:
            messages.error(request, message)
            return render(request, 'registration/verify_otp.html', {'form': form, 'phone': phone})

        messages.success(request, message)
        # Mark OTP as verified in session
        request.session['password_reset_verified'] = True
        request.session['password_reset_phone'] = phone
        return redirect('reset_password')


class ResetPasswordView(View):
    """Step 3: Set new password after OTP verification."""
    def get(self, request):
        if not request.session.get('password_reset_verified'):
            messages.error(request, 'Please verify your OTP first.')
            return redirect('verify_otp')

        form = PasswordResetForm()
        return render(request, 'registration/reset_password.html', {'form': form})

    def post(self, request):
        if not request.session.get('password_reset_verified'):
            messages.error(request, 'Please verify your OTP first.')
            return redirect('verify_otp')

        phone = request.session.get('password_reset_phone')
        if not phone:
            messages.error(request, 'Session expired. Please start over.')
            return redirect('forgot_password')

        form = PasswordResetForm(request.POST)
        if not form.is_valid():
            return render(request, 'registration/reset_password.html', {'form': form})

        new_password = form.cleaned_data['password1']

        # Find the customer and user
        try:
            customer = Customer.objects.get(phone=phone)
            user = customer.user
        except (Customer.DoesNotExist, User.DoesNotExist):
            messages.error(request, 'Account not found. Please contact support.')
            return redirect('forgot_password')

        # Set new password
        user.set_password(new_password)
        user.save()

        # Clear session
        request.session.pop('password_reset_phone', None)
        request.session.pop('password_reset_verified', None)

        AuditLog.log(
            action=AuditLog.ActionType.PASSWORD_CHANGED,
            description=f"Password reset via OTP for customer {customer.customer_number}.",
            user=user,
            object_type='Customer',
            object_id=customer.pk,
            ip_address=_client_ip(request),
        )

        messages.success(request, 'Your password has been reset successfully. You can now log in with your new password.')
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
