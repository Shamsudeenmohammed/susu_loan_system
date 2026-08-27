import logging
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
from django.utils import timezone
from apps.core.utils import normalize_ghana_phone
from apps.notifications.models import SMSNotification

logger = logging.getLogger('apps.notifications')


def _get_brevo_client():
    """Create and return a Brevo API client."""
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = settings.BREVO_API_KEY
    return sib_api_v3_sdk.TransactionalSMSApi(sib_api_v3_sdk.ApiClient(configuration))


def send_sms(phone_number, message, notification_type='GENERAL',
             customer=None, reference_model='', reference_id=None):
    """
    Send SMS via Brevo Transactional SMS API.
    Always creates an SMSNotification record.
    Returns the notification record.
    """
    phone = normalize_ghana_phone(phone_number)

    notification = SMSNotification.objects.create(
        customer=customer,
        phone_number=phone,
        message=message,
        notification_type=notification_type,
        reference_model=reference_model,
        reference_id=reference_id,
        status=SMSNotification.Status.PENDING,
    )

    if not settings.BREVO_ENABLED:
        logger.info(f"[SMS TEST MODE] To: {phone} | Message: {message}")
        notification.status = SMSNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.brevo_message_id = 'TEST-MODE'
        notification.save(update_fields=['status', 'sent_at', 'brevo_message_id'])
        return notification

    if settings.SMS_TEST_MODE:
        logger.info(f"[SMS TEST] To: {phone} | Message: {message}")
        notification.status = SMSNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.brevo_message_id = 'TEST-MODE'
        notification.save(update_fields=['status', 'sent_at', 'brevo_message_id'])
        return notification

    api_key = settings.BREVO_API_KEY
    if not api_key:
        logger.error("BREVO_API_KEY not configured")
        notification.status = SMSNotification.Status.FAILED
        notification.error_message = 'BREVO_API_KEY not configured'
        notification.save(update_fields=['status', 'error_message'])
        return notification

    try:
        client = _get_brevo_client()
        sender = settings.BREVO_SMS_SENDER

        sms_payload = sib_api_v3_sdk.SendTransacSms(
            sender=sender,
            recipient=phone,
            content=message,
        )

        api_response = client.send_transac_sms(sms_payload)
        notification.brevo_message_id = str(getattr(api_response, 'message_id', ''))
        notification.status = SMSNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.save(update_fields=[
            'brevo_message_id', 'status', 'sent_at'
        ])
        logger.info(f"SMS sent to {phone}, Brevo ID: {notification.brevo_message_id}")

    except ApiException as e:
        notification.status = SMSNotification.Status.FAILED
        notification.error_message = f'API {e.status}: {str(e.reason)[:500]}'
        notification.save(update_fields=['status', 'error_message'])
        logger.error(f"SMS API error to {phone}: {e.status} {e.reason}")

    except Exception as e:
        notification.status = SMSNotification.Status.FAILED
        notification.error_message = str(e)[:500]
        notification.save(update_fields=['status', 'error_message'])
        logger.exception(f"SMS unexpected error to {phone}")

    return notification


def retry_sms(notification_pk):
    """Retry a failed SMS."""
    try:
        notification = SMSNotification.objects.get(pk=notification_pk)
        if notification.status != SMSNotification.Status.FAILED:
            return None
        return send_sms(
            phone_number=notification.phone_number,
            message=notification.message,
            notification_type=notification.notification_type,
            customer=notification.customer,
            reference_model=notification.reference_model,
            reference_id=notification.reference_id,
        )
    except SMSNotification.DoesNotExist:
        return None
