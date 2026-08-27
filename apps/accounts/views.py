from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from .forms import LoginForm, UserRegistrationForm
from apps.customers.models import Customer
from apps.core.utils import generate_unique_number


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
        form = UserRegistrationForm()
        return render(request, 'registration/register.html', {'form': form})

    def post(self, request):
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.role = 'CUSTOMER'
            user.save()

            Customer.objects.create(
                user=user,
                first_name=user.first_name,
                last_name=user.last_name,
                phone=user.phone if hasattr(user, 'phone') else '',
                email=user.email,
                status='ACTIVE',
            )

            login(request, user)
            messages.success(request, 'Account created successfully!')
            return redirect('customer_dashboard')
        return render(request, 'registration/register.html', {'form': form})
