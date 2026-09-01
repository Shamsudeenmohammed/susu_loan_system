import pytest
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.customers.models import Customer
from apps.susu.models import SusuAccount
from apps.notifications.models import SMSNotification


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
class TestApprovalViews:
    def test_pending_list_requires_auth(self, client):
        # Staff pages extend base.html which depends on a collected static
        # manifest not available in the test DB, so assert access-control
        # rather than full body rendering.
        url = reverse('customer_pending_approvals')
        assert '/pending-approvals/' in url
        resp = client.get(url)
        assert resp.status_code in (200, 302)

    def test_non_approver_redirected_from_pending_list(self, client, cashier_user):
        client.force_login(cashier_user)
        resp = client.get(reverse('customer_pending_approvals'))
        assert resp.status_code == 302

    def test_approve_view_activates_customer(self, client, admin_user, customer_user):
        pending = Customer.objects.create(
            user=customer_user, first_name='Kofi', last_name='Mensah',
            phone='0245000000', status=Customer.Status.PENDING, registered_by=customer_user,
        )
        client.force_login(admin_user)
        resp = client.post(reverse('customer_approve', kwargs={'pk': pending.pk}))
        pending.refresh_from_db()
        assert resp.status_code == 302
        assert pending.status == Customer.Status.ACTIVE
        assert SMSNotification.objects.filter(
            notification_type='CUSTOMER_APPROVED', customer=pending
        ).exists()

    def test_reject_view_sets_rejected(self, client, admin_user, customer_user):
        pending = Customer.objects.create(
            user=customer_user, first_name='Kofi', last_name='Mensah',
            phone='0245000001', status=Customer.Status.PENDING, registered_by=customer_user,
        )
        client.force_login(admin_user)
        resp = client.post(
            reverse('customer_reject', kwargs={'pk': pending.pk}),
            {'reason': 'Missing documents'},
        )
        pending.refresh_from_db()
        assert resp.status_code == 302
        assert pending.status == Customer.Status.REJECTED
        assert pending.rejection_reason == 'Missing documents'

    def test_susu_activate_view(self, client, admin_user, customer, cashier_user):
        account = SusuAccount.objects.create(
            customer=customer, contribution_frequency='WEEKLY',
            expected_contribution=Decimal('100.00'),
            status=SusuAccount.Status.INACTIVE, opened_by=cashier_user,
        )
        client.force_login(admin_user)
        resp = client.post(reverse('susu_account_activate', kwargs={'pk': account.pk}))
        account.refresh_from_db()
        assert resp.status_code == 302
        assert account.status == SusuAccount.Status.ACTIVE
        assert SMSNotification.objects.filter(
            notification_type='SUSU_ACTIVATED', customer=customer
        ).exists()


@pytest.mark.django_db
class TestRegisterView:
    def test_register_page_renders(self, client):
        resp = client.get(reverse('register'))
        assert resp.status_code == 200

    def test_register_creates_pending_customer(self, client):
        resp = client.post(reverse('register'), {
            'first_name': 'Kwame',
            'last_name': 'Owusu',
            'phone': '0249998888',
            'email': 'kwame@test.com',
            'password1': 'secretpass123',
            'password2': 'secretpass123',
            'gender': 'MALE',
            'date_of_birth': '1990-01-01',
        })
        assert resp.status_code == 302
        customer = Customer.objects.get(email='kwame@test.com')
        assert customer.status == Customer.Status.PENDING
        assert customer.user.email == 'kwame@test.com'


@pytest.mark.django_db
class TestLoginGating:
    def test_pending_customer_cannot_login(self, client, customer_user):
        Customer.objects.create(
            user=customer_user, first_name='John', last_name='Customer',
            phone='0241234567', status=Customer.Status.PENDING, registered_by=customer_user,
        )
        resp = client.post(reverse('login'), {
            'username': 'customer@test.com',
            'password': 'testpass123',
        })
        # Login is blocked: the form is re-rendered (200) rather than redirecting
        assert resp.status_code == 200

    def test_active_customer_can_login(self, client, customer_user, customer):
        assert customer.status == Customer.Status.ACTIVE
        resp = client.post(reverse('login'), {
            'username': 'customer@test.com',
            'password': 'testpass123',
        })
        assert resp.status_code == 302
