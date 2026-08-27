from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from apps.core.decorators import role_required
from .models import AuditLog


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def audit_log_list(request):
    logs = AuditLog.objects.select_related('user').all()

    action = request.GET.get('action')
    if action:
        logs = logs.filter(action=action)

    context = {
        'logs': logs[:200],
        'action_choices': AuditLog.ActionType.choices,
    }
    return render(request, 'audit/audit_log_list.html', context)
