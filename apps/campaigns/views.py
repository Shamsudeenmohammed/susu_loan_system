import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.decorators import role_required
from apps.audit.models import AuditLog

from .forms import CampaignForm, TemplateForm, TestSMSSendForm, RecipientSelectionForm
from .models import SMSCampaign, SMSMessageLog, SMSTemplate
from .services.personalization import CAMPAIGN_PLACEHOLDERS, ALL_PLACEHOLDERS
from .services.recipients import resolve_recipients
from .services import sending
from .services.sms_units import segments_for
from .tasks import run_campaign_task, retry_failed_task


CAMPAIGN_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER')
SEND_ROLES = ('SUPER_ADMIN', 'ADMIN')


def _campaign_placeholder_hints(campaign_type):
    supported = CAMPAIGN_PLACEHOLDERS.get(campaign_type, set())
    return [t for k, t in ALL_PLACEHOLDERS.items() if k in supported]


def _estimate_for(campaign, manual_ids):
    """Resolve recipients and return counts + a small sample."""
    campaign.manual_customer_ids = manual_ids
    recipients = resolve_recipients(campaign)
    est = sending.estimate(campaign, recipients)
    sample = [
        {
            'name': c.get_full_name(),
            'phone': c.phone,
            'message': sending.personal_message(campaign, c),
        }
        for c in recipients[:5]
    ]
    return est, sample


@login_required
@role_required(*CAMPAIGN_ROLES)
def campaign_dashboard(request):
    campaigns = SMSCampaign.objects.all()
    status_totals = campaigns.values('status').annotate(n=Count('status'))
    total_units = campaigns.aggregate(u=Sum('sms_units'))['u'] or 0
    recent = campaigns[:8]
    context = {
        'campaigns': campaigns,
        'status_totals': {s['status']: s['n'] for s in status_totals},
        'total_units': total_units,
        'recent': recent,
        'status_choices': SMSCampaign.Status.choices,
    }
    return render(request, 'campaigns/dashboard.html', context)


@login_required
@role_required(*SEND_ROLES)
def campaign_create(request):
    form = CampaignForm(request.POST or None)
    manual_form = RecipientSelectionForm(
        request.POST or None,
        initial={'customers': request.POST.getlist('customers')} if request.POST else None,
    )
    preview = None
    manual_ids = []
    estimate = None
    sample = []

    if request.POST:
        action = request.POST.get('action')

        if action == 'preview':
            if form.is_valid():
                campaign = form.save(commit=False)
                campaign.created_by = request.user
                if campaign.target_group == SMSCampaign.TargetGroup.MANUAL_SELECTION:
                    manual_ids = [int(pk) for pk in request.POST.getlist('customers') if pk]
                estimate, sample = _estimate_for(campaign, manual_ids)
                preview = {
                    'valid': campaign.valid_phone_count,
                    'missing': campaign.missing_phone_count,
                    'recipients': campaign.recipient_count,
                    'segments': estimate['segments'] if estimate else 0,
                    'units': estimate['units'] if estimate else 0,
                    'sample': sample,
                }
            # Re-render the form with the submitted data so the user can confirm.
            return render(request, 'campaigns/campaign_form.html', {
                'form': form,
                'manual_form': manual_form,
                'preview': preview,
                'estimate': estimate,
                'sample': sample,
                'placeholder_hints': [t for _, t in ALL_PLACEHOLDERS.items()],
            })

        if action == 'create':
            if not request.POST.get('confirm') == '1':
                messages.error(request, 'Please confirm the campaign before sending.')
                return render(request, 'campaigns/campaign_form.html', {
                    'form': form, 'manual_form': manual_form, 'preview': preview,
                })
            if form.is_valid():
                campaign = form.save(commit=False)
                campaign.created_by = request.user
                campaign.uid = sending.generate_campaign_uid()
                if campaign.target_group == SMSCampaign.TargetGroup.MANUAL_SELECTION:
                    campaign.manual_customer_ids = [
                        int(pk) for pk in request.POST.getlist('customers') if pk
                    ]
                if campaign.trigger == SMSCampaign.Trigger.SCHEDULE:
                    campaign.status = SMSCampaign.Status.SCHEDULED
                else:
                    campaign.status = SMSCampaign.Status.DRAFT
                campaign.save()
                sending.audit(
                    'CAMPAIGN_CREATED',
                    f"Created SMS campaign '{campaign.name}' targeting {campaign.target_group}.",
                    request.user, campaign,
                )
                # Persist the recipient ledger so counts show immediately.
                sending.prepare_campaign(campaign, actor=request.user)
                if campaign.status == SMSCampaign.Status.DRAFT:
                    campaign.status = SMSCampaign.Status.SENDING
                    campaign.save(update_fields=['status'])
                    run_campaign_task.delay(campaign.pk)
                    messages.success(request, 'Campaign queued for sending.')
                else:
                    messages.success(request, 'Campaign scheduled.')
                return redirect('campaign_detail', pk=campaign.pk)
            messages.error(request, 'Please correct the errors below.')

    context = {
        'form': form,
        'manual_form': manual_form,
        'preview': preview,
        'estimate': estimate,
        'sample': sample,
        'placeholder_hints': [t for _, t in ALL_PLACEHOLDERS.items()],
    }
    return render(request, 'campaigns/campaign_form.html', context)


@login_required
@role_required(*CAMPAIGN_ROLES)
def campaign_history(request):
    campaigns = SMSCampaign.objects.select_related('created_by').all()
    status = request.GET.get('status')
    q = request.GET.get('q')
    if status:
        campaigns = campaigns.filter(status=status)
    if q:
        campaigns = campaigns.filter(name__icontains=q)
    paginator = Paginator(campaigns, 20)
    page = paginator.get_page(request.GET.get('page'))
    context = {
        'page': page,
        'status_choices': SMSCampaign.Status.choices,
        'status': status,
        'q': q,
    }
    return render(request, 'campaigns/history.html', context)


@login_required
@role_required(*CAMPAIGN_ROLES)
def campaign_detail(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    logs = campaign.message_logs.select_related('customer').all()
    log_status = request.GET.get('log_status')
    if log_status:
        logs = logs.filter(status=log_status)
    paginator = Paginator(logs, 25)
    page = paginator.get_page(request.GET.get('page'))
    context = {
        'campaign': campaign,
        'page': page,
        'log_status_choices': SMSMessageLog.Status.choices,
        'log_status': log_status,
        'placeholder_hints': _campaign_placeholder_hints(campaign.campaign_type),
    }
    return render(request, 'campaigns/campaign_detail.html', context)


@login_required
@role_required(*SEND_ROLES)
def campaign_send_test(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    form = TestSMSSendForm(request.POST or None, initial={'message': campaign.message})
    if request.method == 'POST' and form.is_valid():
        from apps.notifications.services.sms import get_sms_service
        service = get_sms_service()
        notification = service.send_sms(
            phone_number=form.cleaned_data['phone_number'],
            message=form.cleaned_data['message'],
            notification_type='GENERAL',
            customer=None,
            reference_model='SMSCampaign',
            reference_id=campaign.pk,
            unique_key=f"test:{campaign.uid}:{timezone.now().timestamp()}",
        )
        if notification.status == 'SENT':
            messages.success(request, f'Test SMS sent to {form.cleaned_data["phone_number"]}.')
        else:
            messages.error(request, 'Test SMS failed to send.')
        sending.audit('CAMPAIGN_TEST_SMS',
                      f"Sent a test SMS for campaign '{campaign.name}'.",
                      request.user, campaign)
        return redirect('campaign_detail', pk=pk)
    return render(request, 'campaigns/campaign_form.html', {
        'form': CampaignForm(instance=campaign),
        'manual_form': RecipientSelectionForm(),
        'test_form': form,
        'campaign': campaign,
    })


@login_required
@role_required(*SEND_ROLES)
def campaign_retry(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    if not campaign.failed_count:
        messages.info(request, 'No failed messages to retry.')
        return redirect('campaign_detail', pk=pk)
    campaign.refresh_statistics()
    retry_failed_task.delay(campaign.pk)
    sending.audit('CAMPAIGN_RETRY',
                  f"Retried failed messages for campaign '{campaign.name}'.", request.user, campaign)
    messages.success(request, 'Retrying failed messages in the background.')
    return redirect('campaign_detail', pk=pk)


@login_required
@role_required(*SEND_ROLES, 'MANAGER')
def campaign_cancel(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    if campaign.status not in (SMSCampaign.Status.DRAFT, SMSCampaign.Status.SCHEDULED,
                               SMSCampaign.Status.SENDING):
        messages.error(request, 'This campaign cannot be cancelled.')
        return redirect('campaign_detail', pk=pk)
    campaign.status = SMSCampaign.Status.CANCELLED
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=['status', 'completed_at'])
    sending.audit('CAMPAIGN_CANCELLED',
                  f"Cancelled SMS campaign '{campaign.name}'.", request.user, campaign)
    messages.success(request, 'Campaign cancelled.')
    return redirect('campaign_detail', pk=pk)


@login_required
@role_required(*CAMPAIGN_ROLES)
def campaign_export(request, pk):
    campaign = get_object_or_404(SMSCampaign, pk=pk)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="campaign-{campaign.pk}-logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Customer', 'Phone', 'Status', 'SMS Units', 'Sent At', 'Error'])
    for log in campaign.message_logs.all():
        writer.writerow([
            log.customer.get_full_name() if log.customer else '',
            log.phone_number,
            log.status,
            log.sms_units,
            log.sent_at.strftime('%Y-%m-%d %H:%M:%S') if log.sent_at else '',
            log.error_message,
        ])
    return response


@login_required
@role_required(*CAMPAIGN_ROLES)
def campaign_logs(request):
    logs = SMSMessageLog.objects.select_related('campaign', 'customer').all()
    status = request.GET.get('status')
    if status:
        logs = logs.filter(status=status)
    paginator = Paginator(logs, 25)
    page = paginator.get_page(request.GET.get('page'))
    context = {
        'page': page,
        'status_choices': SMSMessageLog.Status.choices,
        'status': status,
    }
    return render(request, 'campaigns/logs.html', context)


@login_required
@role_required(*CAMPAIGN_ROLES)
def template_list(request):
    templates = SMSTemplate.objects.all()
    context = {'templates': templates}
    return render(request, 'campaigns/template_list.html', context)


@login_required
@role_required(*SEND_ROLES)
def template_create(request):
    form = TemplateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        template = form.save(commit=False)
        template.created_by = request.user
        template.save()
        AuditLog.log('TEMPLATE_CREATED',
                     f"Created SMS template '{template.name}'.", request.user,
                     object_type='SMSTemplate', object_id=template.pk,
                     ip_address=request.META.get('REMOTE_ADDR'))
        messages.success(request, 'Template created.')
        return redirect('template_list')
    return render(request, 'campaigns/template_form.html', {'form': form})


@login_required
@role_required(*SEND_ROLES)
def template_edit(request, pk):
    template = get_object_or_404(SMSTemplate, pk=pk)
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == 'POST' and form.is_valid():
        form.save()
        AuditLog.log('TEMPLATE_UPDATED',
                     f"Updated SMS template '{template.name}'.", request.user,
                     object_type='SMSTemplate', object_id=template.pk,
                     ip_address=request.META.get('REMOTE_ADDR'))
        messages.success(request, 'Template updated.')
        return redirect('template_list')
    return render(request, 'campaigns/template_form.html', {'form': form, 'template': template})


@login_required
@role_required(*SEND_ROLES)
def template_delete(request, pk):
    template = get_object_or_404(SMSTemplate, pk=pk)
    if request.method == 'POST':
        AuditLog.log('TEMPLATE_DELETED',
                     f"Deleted SMS template '{template.name}'.", request.user,
                     object_type='SMSTemplate', object_id=template.pk,
                     ip_address=request.META.get('REMOTE_ADDR'))
        template.delete()
        messages.success(request, 'Template deleted.')
    return redirect('template_list')
