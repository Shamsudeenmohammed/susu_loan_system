import pytest
from unittest.mock import patch, MagicMock
from apps.notifications.services.sms import send_sms, retry_sms
from apps.notifications.models import SMSNotification


@pytest.mark.django_db
class TestSMS:
    def test_sms_disabled_creates_record(self, customer):
        notification = send_sms(
            phone_number='0241234567',
            message='Test message',
            notification_type='GENERAL',
            customer=customer,
        )
        assert notification.pk is not None
        assert notification.status == SMSNotification.Status.SENT
        assert notification.brevo_message_id == 'TEST-MODE'

    def test_sms_test_mode(self, customer):
        notification = send_sms(
            phone_number='0241234567',
            message='Test message',
            customer=customer,
        )
        assert notification.status == SMSNotification.Status.SENT

    def test_sms_phone_normalization(self, customer):
        notification = send_sms(
            phone_number='0241234567',
            message='Test',
            customer=customer,
        )
        assert notification.phone_number == '+233241234567'

    @patch('apps.notifications.services.sms._get_brevo_client')
    @patch('apps.notifications.services.sms.settings')
    def test_sms_api_success(self, mock_settings, mock_get_client, customer):
        mock_settings.BREVO_ENABLED = True
        mock_settings.SMS_TEST_MODE = False
        mock_settings.BREVO_API_KEY = 'test-key'
        mock_settings.BREVO_SMS_SENDER = 'TEST'

        mock_api_response = MagicMock()
        mock_api_response.message_id = 'abc-123'

        mock_client = MagicMock()
        mock_client.send_transac_sms.return_value = mock_api_response
        mock_get_client.return_value = mock_client

        notification = send_sms(
            phone_number='0241234567',
            message='Test API',
            customer=customer,
        )
        assert notification.status == SMSNotification.Status.SENT
        assert notification.brevo_message_id == 'abc-123'

    @patch('apps.notifications.services.sms._get_brevo_client')
    @patch('apps.notifications.services.sms.settings')
    def test_sms_api_failure(self, mock_settings, mock_get_client, customer):
        from sib_api_v3_sdk.rest import ApiException

        mock_settings.BREVO_ENABLED = True
        mock_settings.SMS_TEST_MODE = False
        mock_settings.BREVO_API_KEY = 'test-key'
        mock_settings.BREVO_SMS_SENDER = 'TEST'

        mock_client = MagicMock()
        mock_client.send_transac_sms.side_effect = ApiException(status=400, reason='Bad Request')
        mock_get_client.return_value = mock_client

        notification = send_sms(
            phone_number='0241234567',
            message='Test Fail',
            customer=customer,
        )
        assert notification.status == SMSNotification.Status.FAILED
        assert notification.error_message

    def test_retry_sms(self, customer):
        notification = SMSNotification.objects.create(
            customer=customer,
            phone_number='+233241234567',
            message='Failed msg',
            status=SMSNotification.Status.FAILED,
        )
        new_notification = retry_sms(notification.pk)
        assert new_notification is not None
        assert new_notification.status == SMSNotification.Status.SENT

    def test_sms_recorded(self, customer):
        send_sms(phone_number='0241234567', message='Recorded', customer=customer)
        assert SMSNotification.objects.filter(customer=customer).count() == 1
