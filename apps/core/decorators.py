from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps


def role_required(*allowed_roles):
    """Decorator that checks if a user has one of the allowed roles."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.error(request, 'Please log in to continue.')
                return redirect('login')
            if not request.user.has_role(*allowed_roles) and not request.user.is_superuser:
                messages.error(request, 'You do not have permission to access this page.')
                return redirect('dashboard')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def customer_ownership_required(view_func):
    """Decorator that ensures customers can only access their own data."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.has_role('CUSTOMER'):
            if not hasattr(request.user, 'customer_profile'):
                messages.error(request, 'No customer profile found.')
                return redirect('customer_dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper
