import pytest
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.customers.models import Customer
from apps.customers.services import approve_customer, reject_customer
from apps.susu.models import SusuAccount
from apps.susu.services import activate_susu_account
from apps.audit.models import AuditLog
from apps.notifications.models import SMSNotification

User = get_user_model()


@pytest.fixture
def pending_customer(customer_user):
    return Customer.objects.create(
        user=customer_user,
        first_name='John',
        last_name='Customer',
        phone='0241234567',
        status=Customer.Status.PENDING,
        registered_by=customer_user,
    )


@pytest.fixture
def new_user():
    return User.objects.create_user(
        username='kofi_test',
        email='kofi@test.com',
        password='testpass123',
        first_name='Kofi',
        last_name='Mensah',
        role='CUSTOMER',
    )


@pytest.mark.django_db
class TestCustomerApproval:
    def test_approve_transitions_to_active(self, pending_customer, admin_user):
        result = approve_customer(pending_customer, actor=admin_user, ip_address='127.0.0.1')

        pending_customer.refresh_from_db()
        assert result.changed is True
        assert result.sms_sent is True
        assert pending_customer.status == Customer.Status.ACTIVE
        assert pending_customer.is_approved_active
        assert pending_customer.approved_at is not None
        assert pending_customer.approved_by == admin_user

    def test_approve_logs_audit_and_sms(self, pending_customer, admin_user):
        approve_customer(pending_customer, actor=admin_user, ip_address='127.0.0.1')

        assert AuditLog.objects.filter(
            action=AuditLog.ActionType.CUSTOMER_APPROVED,
            object_type='Customer',
            object_id=pending_customer.pk,
            user=admin_user,
        ).exists()

        sms = SMSNotification.objects.filter(
            notification_type='CUSTOMER_APPROVED',
            customer=pending_customer,
        )
        assert sms.exists()
        assert sms.first().status in (SMSNotification.Status.SENT, SMSNotification.Status.DELIVERED)

    def test_duplicate_approve_is_noop_no_duplicate_sms(self, pending_customer, admin_user):
        first = approve_customer(pending_customer, actor=admin_user)
        second = approve_customer(pending_customer, actor=admin_user)

        assert first.changed is True
        assert second.changed is False
        assert second.sms_sent is True  # still true: SMS was already sent
        assert SMSNotification.objects.filter(
            notification_type='CUSTOMER_APPROVED', customer=pending_customer
        ).count() == 1

    def test_reject_sets_rejected(self, pending_customer, admin_user):
        result = reject_customer(
            pending_customer, actor=admin_user, reason='Incomplete documents'
        )

        pending_customer.refresh_from_db()
        assert result.changed is True
        assert result.sms_sent is True
        assert pending_customer.status == Customer.Status.REJECTED
        assert pending_customer.is_rejected
        assert pending_customer.rejection_reason == 'Incomplete documents'
        assert pending_customer.rejected_by == admin_user
        assert SMSNotification.objects.filter(
            notification_type='CUSTOMER_REJECTED', customer=pending_customer
        ).exists()

    def test_reject_idempotent(self, pending_customer, admin_user):
        reject_customer(pending_customer, actor=admin_user, reason='x')
        second = reject_customer(pending_customer, actor=admin_user, reason='y')

        assert second.changed is False
        assert SMSNotification.objects.filter(
            notification_type='CUSTOMER_REJECTED', customer=pending_customer
        ).count() == 1

    def test_reject_preserves_profile(self, pending_customer, new_user, admin_user):
        # Rejected customers keep their data so nothing crashes
        pending_customer.user = new_user
        pending_customer.save()
        reject_customer(pending_customer, actor=admin_user, reason='test')
        pending_customer.refresh_from_db()
        assert pending_customer.first_name == 'John'
        assert pending_customer.user == new_user


@pytest.mark.django_db
class TestSusuActivation:
    def test_activate_transitions_to_active(self, customer, cashier_user):
        account = SusuAccount.objects.create(
            customer=customer,
            contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'),
            status=SusuAccount.Status.INACTIVE,
            opened_by=cashier_user,
        )

        result = activate_susu_account(account, actor=admin_actor(cashier_user), ip_address='127.0.0.1')

        account.refresh_from_db()
        assert result.changed is True
        assert result.sms_sent is True
        assert account.status == SusuAccount.Status.ACTIVE
        assert account.activated_at is not None

    def test_activate_sms_and_audit(self, customer, cashier_user):
        account = SusuAccount.objects.create(
            customer=customer,
            contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'),
            status=SusuAccount.Status.INACTIVE,
            opened_by=cashier_user,
        )
        user = admin_actor(cashier_user)
        activate_susu_account(account, actor=user, ip_address='127.0.0.1')

        assert AuditLog.objects.filter(
            action=AuditLog.ActionType.SUSU_ACCOUNT_ACTIVATED,
            object_id=account.pk,
            user=user,
        ).exists()
        assert SMSNotification.objects.filter(
            notification_type='SUSU_ACTIVATED', customer=customer
        ).exists()

    def test_duplicate_activate_no_duplicate_sms(self, customer, cashier_user):
        account = SusuAccount.objects.create(
            customer=customer,
            contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'),
            status=SusuAccount.Status.INACTIVE,
            opened_by=cashier_user,
        )
        user = admin_actor(cashier_user)

        first = activate_susu_account(account, actor=user)
        second = activate_susu_account(account, actor=user)

        assert first.changed is True
        assert second.changed is False
        assert SMSNotification.objects.filter(
            notification_type='SUSU_ACTIVATED', customer=customer
        ).count() == 1


def admin_actor(user):
    """Promote a user to a role that can perform admin actions."""
    user.role = 'ADMIN'
    user.save()
    return user
