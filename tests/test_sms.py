import pytest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from apps.notifications.services.sms import (
    send_sms,
    retry_sms,
    get_sms_service,
    SMSValidationError,
)
from apps.notifications.services import messages as templates
from apps.notifications.models import SMSNotification
from apps.notifications.providers.sailup import SailupSMSService
from apps.notifications.providers.exceptions import (
    SMSProviderAuthError,
    SMSProviderUnavailableError,
)
from apps.core.utils import normalize_ghana_phone


def fake_response(status_code, payload=None, text=''):
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        text=text,
    )


@pytest.fixture
def real_sms_settings(settings):
    settings.SMS_PROVIDER = 'sailup'
    settings.SAILUP_API_KEY = 'sailup_testkey123'
    settings.SAILUP_BASE_URL = 'https://api.sailup.io/v1'
    settings.SAILUP_SENDER_ID = 'ZEMZEM'
    settings.SAILUP_TIMEOUT = 10
    settings.SAILUP_ENABLED = True
    settings.SMS_TEST_MODE = False
    return settings


@pytest.mark.django_db
class TestSendSMS:
    def test_sms_disabled_creates_record(self, customer):
        # SAILUP_ENABLED=False by default in test settings -> TEST-MODE
        notification = send_sms(
            phone_number='0241234567',
            message='Test message',
            notification_type='GENERAL',
            customer=customer,
        )
        assert notification.pk is not None
        assert notification.status == SMSNotification.Status.SENT
        assert notification.provider_message_id == 'TEST-MODE'

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

    def test_phone_normalization_utility(self):
        assert normalize_ghana_phone('0241234567') == '+233241234567'
        assert normalize_ghana_phone('0201234567') == '+233201234567'
        assert normalize_ghana_phone('0501234567') == '+233501234567'
        assert normalize_ghana_phone('0541234567') == '+233541234567'
        assert normalize_ghana_phone('233241234567') == '+233241234567'
        assert normalize_ghana_phone('+233241234567') == '+233241234567'

    def test_invalid_phone_number_raises(self, customer):
        with pytest.raises(SMSValidationError):
            send_sms(
                phone_number='1234',
                message='Invalid',
                customer=customer,
            )
        # A FAILED record should still be logged with the error
        assert SMSNotification.objects.filter(
            customer=customer, status=SMSNotification.Status.FAILED
        ).exists()

    def test_missing_api_key_logs_without_crash(self, customer, settings):
        # Sailup not configured (no key + disabled) should gracefully skip
        # (TEST-MODE) and never crash / never depend on the financial flow.
        settings.SAILUP_API_KEY = ''
        settings.SMS_TEST_MODE = True
        settings.SAILUP_ENABLED = False
        service = get_sms_service(SailupSMSService(api_key=''))
        notification = service.send_sms(
            phone_number='0241234567',
            message='no key',
            customer=customer,
        )
        assert notification.pk is not None
        assert notification.status == SMSNotification.Status.SENT  # skipped, not crashed

    def test_provider_missing_key_raises_auth_error(self):
        provider = SailupSMSService(api_key='')
        with pytest.raises(SMSProviderAuthError):
            provider.send_sms('+233241234567', 'hello')


@pytest.mark.django_db
class TestSailupProvider:
    def test_send_sms_success(self, real_sms_settings, customer):
        with patch('apps.notifications.providers.sailup.requests.post') as mock_post:
            mock_post.return_value = fake_response(
                202, {'id': '58cf292d-417e-4f61', 'status': 'queued', 'delivery_status': ''}
            )
            notification = send_sms(
                phone_number='0241234567',
                message='Hello',
                customer=customer,
            )
        assert notification.status == SMSNotification.Status.SENT
        assert notification.provider == 'sailup'
        assert notification.provider_message_id == '58cf292d-417e-4f61'
        assert notification.delivery_status == 'queued'

    def test_auth_failure(self, real_sms_settings, customer):
        with patch('apps.notifications.providers.sailup.requests.post') as mock_post:
            mock_post.return_value = fake_response(
                401, {'detail': 'invalid key'}, 'unauthorized'
            )
            notification = send_sms(
                phone_number='0241234567',
                message='Hello',
                customer=customer,
            )
        assert notification.status == SMSNotification.Status.FAILED
        assert '401' in notification.error_message

    def test_network_failure(self, real_sms_settings, customer):
        import requests
        with patch('apps.notifications.providers.sailup.requests.post',
                   side_effect=requests.exceptions.ConnectionError('boom')):
            service = get_sms_service()
            from apps.notifications.providers.exceptions import SMSProviderUnavailableError
            with pytest.raises(SMSProviderUnavailableError):
                service.provider.send_sms('+233241234567', 'hi')

    def test_provider_retry_count(self, real_sms_settings, customer):
        # Simulate first send failing, then retry() increments retry_count
        import requests
        with patch('apps.notifications.providers.sailup.requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError('net')
            service = get_sms_service()
            try:
                service.send_sms(phone_number='0241234567', message='x', customer=customer)
            except Exception:
                pass

        failed = SMSNotification.objects.filter(customer=customer).order_by('-created_at').first()
        assert failed.status == SMSNotification.Status.FAILED
        assert failed.retry_count == 0

        with patch('apps.notifications.providers.sailup.requests.post') as mock_post:
            mock_post.return_value = fake_response(
                202, {'id': 'new-msg', 'status': 'queued', 'delivery_status': ''}
            )
            new = retry_sms(failed.pk)
        assert new is not None
        assert new.status == SMSNotification.Status.SENT
        assert new.retry_count == 1


@pytest.mark.django_db
class TestDuplicateProtection:
    def test_duplicate_unique_key_suppressed(self, customer):
        send_sms(
            phone_number='0241234567',
            message='First',
            customer=customer,
            unique_key='contribution:99',
        )
        # Second attempt with same unique key must not create a second send record
        send_sms(
            phone_number='0241234567',
            message='First',
            customer=customer,
            unique_key='contribution:99',
        )
        assert SMSNotification.objects.filter(unique_key='contribution:99').count() == 1


@pytest.mark.django_db
class TestTaskMessages:
    def test_contribution_sms(self, customer, susu_account):
        from apps.payments.models import Transaction
        from apps.notifications.tasks import send_contribution_sms
        txn = Transaction.objects.create(
            customer=customer,
            account=susu_account,
            transaction_type='SUSU_CONTRIBUTION',
            amount=Decimal('100.00'),
            balance_before=Decimal('1150.00'),
            balance_after=Decimal('1250.00'),
            payment_method='CASH',
        )
        expected = templates.contribution_received(Decimal('100.00'), Decimal('1250.00'), txn.transaction_number)
        assert 'contribution of ghs 100.00' in expected.lower()
        assert 'ghs 1,250.00' in expected.lower()
        send_contribution_sms(txn.pk)
        assert SMSNotification.objects.filter(
            customer=customer, notification_type='CONTRIBUTION'
        ).exists()

    def test_loan_application_sms(self, customer, loan_product, cashier_user):
        from apps.loans.models import Loan
        from apps.notifications.tasks import send_loan_application_sms
        loan = Loan.objects.create(
            customer=customer,
            loan_product=loan_product,
            principal_amount=Decimal('2000.00'),
            term_months=6,
            status='SUBMITTED',
        )
        send_loan_application_sms(loan.pk)
        assert SMSNotification.objects.filter(
            customer=customer, notification_type='LOAN_APPLICATION'
        ).exists()
        msg = templates.loan_application_submitted(loan.loan_number, Decimal('2000.00'))
        assert loan.loan_number in msg

    def test_loan_approved_sms(self, customer, loan_product, cashier_user):
        from apps.loans.models import Loan
        from apps.notifications.tasks import send_loan_approved_sms
        loan = Loan.objects.create(
            customer=customer,
            loan_product=loan_product,
            principal_amount=Decimal('2000.00'),
            term_months=6,
            status='APPROVED',
        )
        send_loan_approved_sms(loan.pk)
        assert SMSNotification.objects.filter(
            customer=customer, notification_type='LOAN_APPROVED'
        ).exists()

    def test_loan_repayment_sms(self, customer, loan_product, cashier_user):
        from apps.loans.models import Loan, LoanRepayment, RepaymentSchedule
        from apps.notifications.tasks import send_repayment_sms
        loan = Loan.objects.create(
            customer=customer,
            loan_product=loan_product,
            principal_amount=Decimal('2000.00'),
            term_months=6,
            status='ACTIVE',
            outstanding_balance=Decimal('1500.00'),
        )
        schedule = RepaymentSchedule.objects.create(
            loan=loan,
            installment_number=1,
            due_date='2026-09-30',
            principal_due=Decimal('300.00'),
            interest_due=Decimal('40.00'),
            total_due=Decimal('340.00'),
            remaining_balance=Decimal('1500.00'),
        )
        repayment = LoanRepayment.objects.create(
            loan=loan,
            installment=schedule,
            amount=Decimal('340.00'),
            recorded_by=cashier_user,
        )
        send_repayment_sms(repayment.pk)
        assert SMSNotification.objects.filter(
            customer=customer, notification_type='LOAN_REPAYMENT'
        ).exists()

    def test_paystack_verification_before_sms(self, customer, susu_account):
        # Ensure we never queue an SMS for an un-verified transaction.
        # We simulate that the SMS task is only reachable after a Transaction
        # with a recorded balance_after exists.
        from apps.payments.models import Transaction
        txn = Transaction.objects.create(
            customer=customer,
            account=susu_account,
            transaction_type='SUSU_CONTRIBUTION',
            amount=Decimal('50.00'),
            balance_before=Decimal('0.00'),
            balance_after=Decimal('50.00'),
            payment_method='PAYSTACK',
        )
        # No SMS should be created just by saving the transaction; it must be
        # explicitly queued.
        assert SMSNotification.objects.filter(customer=customer).count() == 0

        from apps.notifications.tasks import send_contribution_sms
        send_contribution_sms(txn.pk)
        assert SMSNotification.objects.filter(
            customer=customer, notification_type='CONTRIBUTION'
        ).count() == 1


@pytest.mark.django_db
class TestAPIKeyProtection:
    def test_api_key_not_in_log_message(self, real_sms_settings, customer):
        from apps.notifications.services.sms import SMSService
        service = SMSService()
        # error_message must never contain a live key
        with patch('apps.notifications.providers.sailup.requests.post') as mock_post:
            mock_post.return_value = type('Resp', (), {
                'status_code': 500,
                'json': lambda: {'detail': 'server error'},
                'text': 'server error',
            })()
            notification = service.send_sms(
                phone_number='0241234567', message='x', customer=customer
            )
        assert 'sailup_testkey123' not in notification.error_message
        assert SMSNotification.objects.filter(
            customer=customer, status='FAILED'
        ).exists()

    def test_api_key_not_stored_on_model(self, real_sms_settings, customer):
        notification = send_sms(phone_number='0241234567', message='x', customer=customer)
        # No field should contain the API key
        leaked = False
        for field in notification._meta.concrete_fields:
            val = getattr(notification, field.name)
            if isinstance(val, str) and 'sailup_testkey123' in val:
                leaked = True
        assert not leaked
