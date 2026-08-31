import logging

from django.conf import settings
from django.utils import timezone

from apps.core.utils import normalize_ghana_phone, validate_ghana_phone
from apps.notifications.models import SMSNotification
from apps.notifications.providers import get_provider
from apps.notifications.providers.exceptions import (
    SMSProviderAuthError,
    SMSProviderError,
    SMSProviderUnavailableError,
)

logger = logging.getLogger('apps.notifications')


class SMSValidationError(ValueError):
    """Raised when a phone number is invalid and cannot be sent."""


class SMSService:
    """
    High-level SMS notification service.

    Business logic calls this service instead of talking to an SMS provider
    directly. It is responsible for:
      - phone normalization + validation
      - SMS logging (SMSNotification records)
      - duplicate protection
      - provider dispatch via the provider factory
      - retry bookkeeping
      - graceful handling when SMS is disabled / unconfigured
    """

    def __init__(self, provider=None):
        self.provider = provider or get_provider()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_sms(self, phone_number, message, notification_type='GENERAL',
                 customer=None, reference_model='', reference_id=None,
                 unique_key=''):
        """
        Queue and send an SMS.

        Returns an SMSNotification record. This method is safe to call even
        when SMS is disabled or unconfigured: it will not raise, it will just
        log an appropriate record.

        Raises SMSValidationError if the phone number is invalid.
        """
        phone = normalize_ghana_phone(phone_number)

        # Duplicate protection: if a record with the same unique_key already
        # exists and is in a terminal/final state, do not send again.
        if unique_key and self._duplicate_exists(unique_key):
            logger.info(f"SMS duplicate suppressed for unique_key={unique_key}")
            return self._latest_for(unique_key)

        if not validate_ghana_phone(phone):
            notification = SMSNotification.objects.create(
                customer=customer,
                phone_number=phone or '',
                message=message,
                notification_type=notification_type,
                reference_model=reference_model,
                reference_id=reference_id,
                unique_key=unique_key,
                status=SMSNotification.Status.FAILED,
                error_message='Invalid phone number',
            )
            raise SMSValidationError(f'Invalid phone number: {phone_number}')

        notification = SMSNotification.objects.create(
            customer=customer,
            phone_number=phone,
            message=message,
            notification_type=notification_type,
            reference_model=reference_model,
            reference_id=reference_id,
            unique_key=unique_key,
            status=SMSNotification.Status.QUEUED,
        )

        return self._dispatch(notification)

    def send_sms_async(self, *args, **kwargs):
        """Schedule the SMS via Celery. Never blocks the financial flow."""
        from apps.notifications.tasks import send_sms_task
        send_sms_task.delay(*args, **kwargs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _dispatch(self, notification):
        """Actually dispatch a notification through the provider."""
        if not self._running():
            self._mark_test_mode(notification)
            return notification

        provider = self.provider
        provider_name = getattr(provider, 'name', 'unknown')

        notification.provider = provider_name
        notification.status = SMSNotification.Status.SENDING
        notification.save(update_fields=['provider', 'status'])

        try:
            result = provider.send_sms(
                phone_number=notification.phone_number,
                message=notification.message,
            )
        except SMSProviderAuthError as exc:
            self._mark_failed(notification, str(exc))
        except (SMSProviderUnavailableError, SMSProviderError) as exc:
            self._mark_failed(notification, str(exc))
        except Exception as exc:
            logger.exception("Unexpected SMS provider error")
            self._mark_failed(notification, f'Unexpected error: {exc}')

        else:
            notification.provider_message_id = result.get('provider_message_id', '')
            notification.delivery_status = result.get('status', '')
            notification.status = SMSNotification.Status.SENT
            notification.sent_at = timezone.now()
            notification.save(update_fields=[
                'provider_message_id', 'delivery_status', 'status', 'sent_at',
            ])
            logger.info(f"SMS sent to {notification.phone_number}, "
                        f"provider id: {notification.provider_message_id}")

        return notification

    def _running(self):
        """Whether real SMS sending is enabled."""
        if not getattr(self.provider, 'enabled', False):
            return False
        if not getattr(settings, 'SAILUP_ENABLED', False):
            return False
        if getattr(settings, 'SMS_TEST_MODE', False):
            return False
        return True

    def _mark_test_mode(self, notification):
        """Mark a notification as sent in local/test (no provider) mode."""
        logger.info(f"[SMS TEST MODE] To: {notification.phone_number} | "
                    f"Msg: {notification.message}")
        notification.status = SMSNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.provider_message_id = 'TEST-MODE'
        notification.save(update_fields=['status', 'sent_at', 'provider_message_id'])
        return notification

    def _mark_failed(self, notification, error_message):
        notification.status = SMSNotification.Status.FAILED
        notification.error_message = error_message[:500]
        notification.save(update_fields=['status', 'error_message'])
        logger.error(f"SMS failed to {notification.phone_number}: {error_message}")
        return notification

    def _duplicate_exists(self, unique_key):
        return SMSNotification.objects.filter(unique_key=unique_key).exists()

    def _latest_for(self, unique_key):
        return SMSNotification.objects.filter(unique_key=unique_key).order_by('-created_at').first()


def get_sms_service(provider=None):
    """Build the active SMS service instance."""
    return SMSService(provider=provider)


def send_sms(*args, **kwargs):
    """
    Shorthand facade: build a service and send. Keeps existing call-sites
    working while routing through the provider abstraction.
    """
    return get_sms_service().send_sms(*args, **kwargs)


def retry_sms(notification_pk):
    """Retry a previously failed SMS notification."""
    try:
        notification = SMSNotification.objects.get(pk=notification_pk)
    except SMSNotification.DoesNotExist:
        return None

    if notification.status != SMSNotification.Status.FAILED:
        return None

    service = get_sms_service()

    new_notification = service.send_sms(
        phone_number=notification.phone_number,
        message=notification.message,
        notification_type=notification.notification_type,
        customer=notification.customer,
        reference_model=notification.reference_model,
        reference_id=notification.reference_id,
    )
    new_notification.retry_count = notification.retry_count + 1
    new_notification.save(update_fields=['retry_count'])
    return new_notification
