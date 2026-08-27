from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from apps.core.decorators import role_required
from .models import SMSNotification
from .services.sms import retry_sms


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def sms_notification_list(request):
    notifications = SMSNotification.objects.all()

    status = request.GET.get('status')
    ntype = request.GET.get('type')

    if status:
        notifications = notifications.filter(status=status)
    if ntype:
        notifications = notifications.filter(notification_type=ntype)

    context = {
        'notifications': notifications[:100],
        'total_count': notifications.count(),
        'status_choices': SMSNotification.Status.choices,
        'type_choices': SMSNotification.NotificationType.choices,
    }
    return render(request, 'notifications/sms_list.html', context)


@login_required
@role_required('SUPER_ADMIN', 'ADMIN', 'MANAGER')
def sms_notification_detail(request, pk):
    notification = get_object_or_404(SMSNotification, pk=pk)
    return render(request, 'notifications/sms_detail.html', {'notification': notification})


@login_required
@role_required('SUPER_ADMIN', 'ADMIN')
def sms_retry(request, pk):
    notification = get_object_or_404(SMSNotification, pk=pk)
    if notification.status != SMSNotification.Status.FAILED:
        messages.warning(request, 'Only failed notifications can be retried.')
        return redirect('sms_notification_detail', pk=pk)

    new_notification = retry_sms(pk)
    if new_notification:
        messages.success(request, f'SMS re-attempted. New notification: {new_notification.notification_number}')
    else:
        messages.error(request, 'Failed to retry SMS.')
    return redirect('sms_notification_detail', pk=pk)
