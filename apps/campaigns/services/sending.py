"""
Campaign sending orchestration.

Responsibilities:
  - Resolve + validate recipients (dynamic query against live data).
  - Build the per-recipient SMSMessageLog ledger.
  - Estimate and persist SMS units.
  - Send each personalized message through the EXISTING SailUp SMS service.
  - Track per-recipient + campaign-level statistics.
  - Retry only failed messages.
"""
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.core.utils import normalize_ghana_phone, validate_ghana_phone
from apps.notifications.services.sms import get_sms_service
from apps.audit.models import AuditLog

from .recipients import resolve_recipients, resolve_slots
from .personalization import build_context, personalize
from .sms_units import segments_for

logger = logging.getLogger('apps.campaigns')

NOTIFICATION_TYPES = {
    'GENERAL_ANNOUNCEMENT': 'GENERAL',
    'REPAYMENT_REMINDER': 'REPAYMENT_REMINDER',
    'OVERDUE_REPAYMENT_REMINDER': 'REPAYMENT_REMINDER',
    'CONTRIBUTION_REMINDER': 'CONTRIBUTION',
    'LOAN_NOTIFICATION': 'LOAN_APPLICATION',
    'ACCOUNT_APPROVAL': 'CUSTOMER_APPROVED',
    'ACCOUNT_ACTIVATION': 'CUSTOMER_APPROVED',
    'SUSU_ACTIVATION': 'SUSU_ACTIVATED',
    'PAYMENT_CONFIRMATION': 'CONTRIBUTION',
    'CUSTOM_MESSAGE': 'GENERAL',
}

# Maps to SMSMessageLog.Status
_NOTIFICATION_TO_LOG = {
    'SENT': 'SENT',
    'DELIVERED': 'DELIVERED',
    'FAILED': 'FAILED',
    'QUEUED': 'SENDING',
    'SENDING': 'SENDING',
}


def generate_campaign_uid():
    return get_random_string(24)


def estimate(campaign, recipients):
    """Compute estimated totals for a recipient list (no DB writes).

    ``recipients`` is a list of ``(customer, susu_account_or_None)`` slots so
    that a single contact with multiple active Susu accounts counts once per
    account.
    """
    segments = segments_for(campaign.message)
    total_units = segments * len(recipients)
    return {
        'recipients': len(recipients),
        'segments': segments,
        'units': total_units,
    }


def _client_ip_from_request(actor):
    return getattr(actor, '_campaign_ip', None)


def audit(action, description, user, campaign):
    AuditLog.log(
        action=action,
        description=description,
        user=user,
        object_type='SMSCampaign',
        object_id=campaign.pk,
        ip_address=_client_ip_from_request(user),
    )


def prepare_campaign(campaign, actor=None):
    """
    Resolve + validate recipient slots, persist the SMSMessageLog ledger and
    campaign counts. Returns the ready campaign. Safe to call repeatedly for
    the same campaign (idempotent via unique_key).

    Recipient slots: one message per ACTIVE Susu account (so a contact with
    several savings accounts receives one SMS per account), and one message
    for customers with no active Susu account.
    """
    from apps.campaigns.models import SMSMessageLog

    customers = resolve_recipients(campaign)
    slots = resolve_slots(customers)

    valid = 0
    missing = 0
    units = 0
    with transaction.atomic():
        for customer, account in slots:
            phone = normalize_ghana_phone(customer.phone)
            account_key = account.pk if account else 'none'
            unique_key = f"campaign:{campaign.pk}:{customer.pk}:account:{account_key}"
            message = personal_message(campaign, customer, account)
            seg = segments_for(campaign.message)
            if not phone or not validate_ghana_phone(phone):
                missing += 1
                SMSMessageLog.objects.get_or_create(
                    unique_key=unique_key,
                    defaults={
                        'campaign': campaign,
                        'customer': customer,
                        'susu_account': account,
                        'phone_number': phone or '',
                        'message': message,
                        'status': SMSMessageLog.Status.REJECTED,
                        'error_message': 'Invalid or missing phone number',
                        'sms_units': seg,
                    },
                )
                continue
            valid += 1
            units += seg
            SMSMessageLog.objects.get_or_create(
                unique_key=unique_key,
                defaults={
                    'campaign': campaign,
                    'customer': customer,
                    'susu_account': account,
                    'phone_number': phone,
                    'message': message,
                    'status': SMSMessageLog.Status.QUEUED,
                    'sms_units': seg,
                },
            )

        campaign.recipient_count = len(slots)
        campaign.valid_phone_count = valid
        campaign.missing_phone_count = missing
        campaign.excluded_count = max(0, len(slots) - valid - missing)
        campaign.sms_units = units
        campaign.save()

    return campaign


def personal_message(campaign, customer, account=None):
    ctx = build_context(customer, campaign.campaign_type, account=account)
    return personalize(campaign.message, ctx)


def run_campaign_impl(campaign_pk):
    """Send a campaign. Executed inside a Celery worker (not the web request)."""
    from apps.campaigns.models import SMSCampaign, SMSMessageLog

    try:
        campaign = SMSCampaign.objects.get(pk=campaign_pk)
    except SMSCampaign.DoesNotExist:
        return

    if campaign.status in (SMSCampaign.Status.COMPLETED, SMSCampaign.Status.CANCELLED):
        # Already finished; never resend a completed/cancelled campaign.
        return

    # Ensure the ledger exists (in case prepare was skipped).
    prepare_campaign(campaign)

    logs = campaign.message_logs.filter(
        status__in=[SMSMessageLog.Status.QUEUED, SMSMessageLog.Status.REJECTED]
    )

    campaign.started_at = campaign.started_at or timezone.now()
    campaign.status = SMSCampaign.Status.SENDING
    campaign.save()

    service = get_sms_service()
    updated = 0
    for log in logs:
        if log.status == SMSMessageLog.Status.REJECTED:
            # Already a known-bad recipient; do not attempt.
            continue
        log.status = SMSMessageLog.Status.SENDING
        log.save(update_fields=['status'])
        try:
            notification = service.send_sms(
                phone_number=log.phone_number,
                message=log.message,
                notification_type=NOTIFICATION_TYPES.get(campaign.campaign_type, 'GENERAL'),
                customer=log.customer,
                reference_model='SMSMessageLog',
                reference_id=log.pk,
                unique_key=f"campaign_send:{campaign.pk}:{log.pk}",
            )
            log.status = _NOTIFICATION_TO_LOG.get(notification.status, 'SENT')
            log.provider_message_id = notification.provider_message_id
            log.delivery_status = notification.delivery_status
            log.error_message = notification.error_message
            log.sent_at = notification.sent_at or timezone.now()
            if notification.status == 'SENT':
                log.sent_at = timezone.now()
            log.save()
            updated += 1
        except Exception as exc:  # never let one recipient stop the campaign
            logger.exception("Campaign send failed for log %s: %s", log.pk, exc)
            log.status = SMSMessageLog.Status.FAILED
            log.error_message = str(exc)[:500]
            log.save(update_fields=['status', 'error_message'])
            continue

    final = campaign.refresh_statistics()
    return final


def retry_failed_impl(campaign_pk):
    """Retry ONLY failed/rejected-quietly messages for a campaign."""
    from apps.campaigns.models import SMSCampaign, SMSMessageLog

    campaign = SMSCampaign.objects.get(pk=campaign_pk)
    failed_logs = campaign.message_logs.filter(
        status__in=[SMSMessageLog.Status.FAILED, SMSMessageLog.Status.REJECTED]
    ).exclude(error_message='Invalid or missing phone number')

    if not failed_logs.exists():
        return campaign, 0

    service = get_sms_service()
    retried = 0
    for log in failed_logs:
        if not log.phone_number or not validate_ghana_phone(log.phone_number):
            log.status = SMSMessageLog.Status.REJECTED
            log.error_message = 'Invalid or missing phone number'
            log.save()
            continue
        log.status = SMSMessageLog.Status.SENDING
        log.error_message = ''
        log.retry_count += 1
        log.save(update_fields=['status', 'error_message', 'retry_count'])
        try:
            notification = service.send_sms(
                phone_number=log.phone_number,
                message=log.message,
                notification_type=NOTIFICATION_TYPES.get(campaign.campaign_type, 'GENERAL'),
                customer=log.customer,
                reference_model='SMSMessageLog',
                reference_id=log.pk,
                unique_key=f"campaign_send:{campaign.pk}:{log.pk}:r{log.retry_count}",
            )
            log.status = _NOTIFICATION_TO_LOG.get(notification.status, 'SENT')
            log.provider_message_id = notification.provider_message_id
            if notification.status == 'FAILED':
                log.error_message = notification.error_message
            log.sent_at = timezone.now()
            log.save()
            retried += 1
        except Exception as exc:
            logger.exception("Retry failed for log %s: %s", log.pk, exc)
            log.status = SMSMessageLog.Status.FAILED
            log.error_message = str(exc)[:500]
            log.save(update_fields=['status', 'error_message'])

    campaign.refresh_statistics()
    return campaign, retried
