from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.decorators import role_required

from .models import School
from .scoping import (
    SESSION_KEY,
    get_active_school,
    is_global_user,
    scope_queryset,
)

User = get_user_model()


@login_required
@require_POST
def switch_school(request):
    """Let a global user (proprietor/admin) narrow the view to one school."""
    if not is_global_user(request.user):
        messages.error(request, 'You can only view your assigned school.')
        return redirect('dashboard')

    school_id = request.POST.get('school') or ''
    if school_id in ('', 'all', '0'):
        request.session.pop(SESSION_KEY, None)
        messages.success(request, 'Now viewing all schools.')
        return redirect(request.POST.get('next') or 'dashboard')

    school = School.objects.filter(pk=school_id, is_active=True).first()
    if school is None:
        messages.error(request, 'That school could not be found.')
        return redirect('dashboard')

    request.session[SESSION_KEY] = school.pk
    messages.success(request, f'Now viewing {school.name}.')
    return redirect(request.POST.get('next') or 'dashboard')


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'PROPRIETOR', 'HEAD_TEACHER')
def staff_list(request):
    """Read-only list of the staff/teachers for the active school.

    ``scope_queryset`` enforces isolation in the backend: a head teacher only
    ever sees their own school's staff, while a global user in the combined view
    sees every school (with the school shown on each row).
    """
    staff = (User.objects.select_related('school')
             .exclude(role='CUSTOMER')
             .filter(is_active=True))
    staff = scope_queryset(staff, request)
    query = (request.GET.get('q') or '').strip()
    if query:
        staff = staff.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(username__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query))
    staff = staff.order_by('school__name', 'first_name', 'last_name')
    return render(request, 'schools/staff_list.html', {
        'staff': staff,
        'query': query,
        'active_school': get_active_school(request),
    })
