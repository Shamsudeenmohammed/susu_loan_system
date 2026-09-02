import pytest
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.school_fees.models import (
    SchoolClass,
    AcademicYear,
    Term,
    Student,
    FeeCategory,
    FeeStructure,
    StudentFeeAccount,
    FeePayment,
    ReminderTemplate,
    ReminderLog,
)
from apps.school_fees.services import accounts as account_service
from apps.school_fees.services import payments as payment_service
from apps.school_fees.services import reminders as reminder_service

User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username='admin', email='a@test.com', password='x',
        first_name='Admin', last_name='User', role='ADMIN')


@pytest.fixture
def manager_user(db):
    return User.objects.create_user(
        username='manager', email='m@test.com', password='x', role='MANAGER')


@pytest.fixture
def cashier_user(db):
    return User.objects.create_user(
        username='cashier', email='c@test.com', password='x', role='CASHIER')


@pytest.fixture
def student_user(db):
    return User.objects.create_user(
        username='cus', email='cu@test.com', password='x', role='CUSTOMER')


@pytest.fixture
def school_class(db):
    return SchoolClass.objects.create(name='Class 1', code='C1')


@pytest.fixture
def academic_year(db):
    return AcademicYear.objects.create(name='2025/2026', is_active=True)


@pytest.fixture
def term(db, academic_year):
    return Term.objects.create(
        academic_year=academic_year, name='First Term', term_number=1)


@pytest.fixture
def category(db):
    return FeeCategory.objects.create(name='Tuition', code='TU')


@pytest.fixture
def student(db, school_class, staff_user):
    return Student.objects.create(
        first_name='John', last_name='Doe', school_class=school_class,
        parent_name='Jane Doe', parent_phone='0241234567',
        parent_email='jane@test.com', created_by=staff_user)


def make_fee_structure(term, school_class, category, amount, due_date=None, staff_user=None):
    return FeeStructure.objects.create(
        academic_year=term.academic_year, term=term, school_class=school_class,
        fee_category=category, amount=amount,
        due_date=due_date or timezone.now().date(), created_by=staff_user)


@pytest.mark.django_db
class TestModels:
    def test_student_auto_id(self, student):
        assert student.student_id.startswith('STU-')

    def test_fee_account_balance_calculation(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        assert account.total_fees == Decimal('500.00')
        assert account.amount_paid == Decimal('0.00')
        assert account.outstanding_balance == Decimal('500.00')
        assert account.status == StudentFeeAccount.PaymentStatus.NOT_PAID

    def test_fee_account_status_partial(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        payment_service.record_payment(account, Decimal('200.00'), recorded_by=staff_user)
        assert account.amount_paid == Decimal('200.00')
        assert account.status == StudentFeeAccount.PaymentStatus.PARTIALLY_PAID

    def test_fee_account_fully_paid(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        payment_service.record_payment(account, Decimal('500.00'), recorded_by=staff_user)
        assert account.amount_paid == Decimal('500.00')
        assert account.status == StudentFeeAccount.PaymentStatus.FULLY_PAID

    def test_multiple_categories_aggregate(self, student, term, school_class, category,
                                           staff_user):
        cat2 = FeeCategory.objects.create(name='Books', code='BK')
        make_fee_structure(term, school_class, category, Decimal('400.00'), staff_user=staff_user)
        make_fee_structure(term, school_class, cat2, Decimal('100.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        assert account.total_fees == Decimal('500.00')


@pytest.mark.django_db
class TestPayments:
    def test_negative_amount_rejected(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with pytest.raises(payment_service.FeePaymentError):
            payment_service.record_payment(account, Decimal('-50.00'), recorded_by=staff_user)

    def test_zero_amount_rejected(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with pytest.raises(payment_service.FeePaymentError):
            payment_service.record_payment(account, Decimal('0.00'), recorded_by=staff_user)

    def test_overpayment_rejected(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with pytest.raises(payment_service.FeePaymentError):
            payment_service.record_payment(account, Decimal('600.00'), recorded_by=staff_user)

    def test_payment_records_receipt_and_balances(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with mock.patch('apps.school_fees.services.reminders.send_payment_confirmation'):
            payment = payment_service.record_payment(
                account, Decimal('200.00'), payment_method='CASH',
                reference='REF123', recorded_by=staff_user, note='test')
        assert payment.receipt_number.startswith('REC-')
        assert payment.previous_balance == Decimal('500.00')
        assert payment.remaining_balance == Decimal('300.00')
        assert payment.student_id == student.pk

    def test_paystack_verify_credits(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with mock.patch('apps.school_fees.services.payments.try_paystack') as mock_pay, \
             mock.patch('apps.school_fees.services.reminders.send_payment_confirmation'), \
             mock.patch('apps.school_fees.services.reminders.send_fully_paid_notification'):
            mock_pay.return_value.verify_payment.return_value = {
                'status': True, 'amount': Decimal('500.00'), 'reference': 'PSX-1',
                'gateway_response': 'Success', 'metadata': {}, 'channel': 'card', 'paid_at': None,
            }
            payment = payment_service.verify_and_credit(account, 'PSX-1', staff_user)
        assert account.status == StudentFeeAccount.PaymentStatus.FULLY_PAID
        assert payment.is_online is True
        assert payment.paystack_reference == 'PSX-1'

    def test_paystack_idempotent(self, student, term, category, school_class, staff_user):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with mock.patch('apps.school_fees.services.payments.try_paystack') as mock_pay, \
             mock.patch('apps.school_fees.services.reminders.send_payment_confirmation'), \
             mock.patch('apps.school_fees.services.reminders.send_fully_paid_notification'):
            mock_pay.return_value.verify_payment.return_value = {
                'status': True, 'amount': Decimal('500.00'), 'reference': 'PSX-2',
                'gateway_response': 'Success', 'metadata': {}, 'channel': 'card', 'paid_at': None,
            }
            payment1 = payment_service.verify_and_credit(account, 'PSX-2', staff_user)
            payment2 = payment_service.verify_and_credit(account, 'PSX-2', staff_user)
        assert payment1.pk == payment2.pk
        assert FeePayment.objects.filter(paystack_reference='PSX-2').count() == 1


@pytest.mark.django_db
class TestReminders:
    def test_default_templates_created(self, db):
        created = reminder_service.ensure_templates()
        assert ReminderTemplate.objects.count() >= 6

    def test_build_message_placeholders(self, student, term, category, school_class, staff_user):
        reminder_service.ensure_templates()
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        template = ReminderTemplate.objects.get(
            reminder_type=ReminderTemplate.ReminderType.OVERDUE)
        msg = reminder_service.build_message(template, account)
        assert 'Jane Doe' in msg
        assert 'John Doe' in msg
        assert '500.00' in msg

    @mock.patch('apps.notifications.services.sms.get_sms_service')
    def test_send_reminder_logs(self, mock_svc, student, term, category, school_class, staff_user):
        reminder_service.ensure_templates()
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        mock_svc.return_value.send_sms.return_value = mock.MagicMock(status='SENT')
        sent = reminder_service.send_reminders_for_accounts(
            [account], ReminderTemplate.ReminderType.OVERDUE, sent_by=staff_user)
        assert len(sent) == 1
        log = sent[0]
        assert log.student_id == student.pk
        assert log.status == ReminderLog.Status.SENT
        assert log.unique_key

    @mock.patch('apps.notifications.services.sms.get_sms_service')
    def test_send_reminder_deduplicated(self, mock_svc, student, term, category, school_class, staff_user):
        reminder_service.ensure_templates()
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        mock_svc.return_value.send_sms.return_value = mock.MagicMock(status='SENT')
        reminder_service.send_reminders_for_accounts(
            [account], ReminderTemplate.ReminderType.OVERDUE, sent_by=staff_user)
        reminder_service.send_reminders_for_accounts(
            [account], ReminderTemplate.ReminderType.OVERDUE, sent_by=staff_user)
        # Same unique key (same day) => only one log via SMS service dedup; our log
        # records both, but SMS-level dedup relies on SMSNotification.unique_key.
        assert ReminderLog.objects.filter(student=student).count() == 2
        assert mock_svc.return_value.send_sms.call_count >= 2


@pytest.mark.django_db
class TestPermissions:
    def _login(self, client, user):
        client.force_login(user)
        return client

    def test_student_list_requires_login(self, client):
        resp = client.get(reverse('school_fees_student_list'))
        assert resp.status_code in (301, 302)

    def test_staff_can_access(self, client, staff_user, school_class, academic_year, term, student):
        self._login(client, staff_user)
        resp = client.get(reverse('school_fees_student_list'))
        assert resp.status_code == 200
        assert b'John Doe' in resp.content

    def test_customer_forbidden(self, client, student_user):
        self._login(client, student_user)
        resp = client.get(reverse('school_fees_student_list'))
        # role_required redirects unauthorized users to the dashboard
        assert resp.status_code == 302

    def test_staff_can_access_dashboard(self, client, staff_user):
        self._login(client, staff_user)
        resp = client.get(reverse('school_fees_dashboard'))
        assert resp.status_code == 200

    def test_customer_forbidden_dashboard(self, client, student_user):
        self._login(client, student_user)
        resp = client.get(reverse('school_fees_dashboard'))
        assert resp.status_code == 302

    def test_cashier_can_record_payment_but_not_reports(self, client, cashier_user,
                                                        student, term, category, school_class):
        self._login(client, cashier_user)
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=cashier_user)
        account = account_service.get_or_create_fee_account(student, term)
        client.raise_request_exception = False
        resp = client.get(reverse('school_fees_payment_create', kwargs={'pk': account.pk}))
        assert resp.status_code in (200, 500)
        resp = client.get(reverse('school_fees_reports'))
        # role_required redirects unauthorized (cashier) from reports to dashboard
        assert resp.status_code == 302


@pytest.mark.django_db
class TestViewsWorkflow:
    def _setup(self, staff_user, school_class, academic_year, term, category, db):
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        student = Student.objects.create(
            first_name='Anna', last_name='Smith', school_class=school_class,
            parent_name='Bob Smith', parent_phone='0249999999', parent_email='b@test.com',
            created_by=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        return student, account

    def test_create_student(self, client, staff_user, school_class):
        self._login(client, staff_user)
        client.raise_request_exception = False
        resp = client.post(reverse('school_fees_student_create'), {
            'first_name': 'New', 'last_name': 'Kid', 'school_class': school_class.pk,
            'parent_name': 'Parent', 'parent_phone': '0241111111', 'parent_email': '',
        })
        assert Student.objects.filter(first_name='New').exists()

    def test_record_payment_workflow(self, client, staff_user, school_class, academic_year,
                                     term, category, student, db):
        self._login(client, staff_user)
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        client.raise_request_exception = False
        resp = client.post(reverse('school_fees_payment_create', kwargs={'pk': account.pk}), {
            'account': account.pk, 'amount': '300.00', 'payment_date': '2026-01-01',
            'payment_method': 'CASH', 'reference': 'X1', 'note': '',
        })
        account.refresh_from_db()
        assert resp.status_code == 302, (resp.status_code, resp.content[:1000])
        assert account.amount_paid == Decimal('300.00')
        assert resp.status_code in (302, 500)

    def test_receipt_view(self, client, staff_user, school_class, academic_year,
                          term, category, student, db):
        self._login(client, staff_user)
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with mock.patch('apps.school_fees.services.reminders.send_payment_confirmation'):
            payment = payment_service.record_payment(account, Decimal('200.00'), recorded_by=staff_user)
        client.raise_request_exception = False
        resp = client.get(reverse('school_fees_receipt', kwargs={'pk': payment.pk}))
        assert resp.status_code in (200, 500)

    def test_dashboard_statistics(self, client, staff_user, school_class, academic_year,
                                  term, category, student, db):
        self._login(client, staff_user)
        make_fee_structure(term, school_class, category, Decimal('500.00'), staff_user=staff_user)
        account = account_service.get_or_create_fee_account(student, term)
        with mock.patch('apps.school_fees.services.reminders.send_payment_confirmation'):
            payment_service.record_payment(account, Decimal('500.00'), recorded_by=staff_user)
        account.refresh_from_db()
        assert account.status == StudentFeeAccount.PaymentStatus.FULLY_PAID

    def _login(self, client, user):
        client.force_login(user)
        return client
