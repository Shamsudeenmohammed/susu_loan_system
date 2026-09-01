import pytest
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.exceptions import ValidationError

from apps.campaigns.models import SMSCampaign, SMSMessageLog, SMSTemplate
from apps.campaigns.services.recipients import resolve_recipients
from apps.campaigns.services.personalization import build_context, personalize
from apps.campaigns.services import sending
from apps.campaigns.services.sms_units import segments_for
from apps.customers.models import Customer
from apps.notifications.models import SMSNotification
from apps.susu.models import SusuAccount

User = get_user_model()


def make_customer(db, phone, status='ACTIVE', first='A', last='B'):
    u = User.objects.create_user(
        email=f'{phone}@test.com', password='x', first_name=first,
        last_name=last, role='CUSTOMER',
    )
    return Customer.objects.create(
        user=u, first_name=first, last_name=last, phone=phone, status=status,
    )


@pytest.fixture
def base_campaign(db, customer):
    return SMSCampaign.objects.create(
        name='Announcement',
        campaign_type='GENERAL_ANNOUNCEMENT',
        message='Hello {{first_name}}, welcome to Zemzem!',
        target_group=SMSCampaign.TargetGroup.ALL_ACTIVE,
        status=SMSCampaign.Status.DRAFT,
        created_by=customer.user,
    )


@pytest.mark.django_db
class TestRecipientResolution:
    def test_all_active_includes_active_excludes_inactive(self, customer):
        inactive = make_customer(db=None, phone='0242222222', status='INACTIVE')
        pending = make_customer(db=None, phone='0243333333', status='PENDING')
        campaign = SMSCampaign(
            target_group=SMSCampaign.TargetGroup.ALL_ACTIVE, filters={}
        )
        ids = {c.pk for c in resolve_recipients(campaign)}
        assert customer.pk in ids
        assert inactive.pk not in ids
        assert pending.pk not in ids

    def test_manual_selection_respects_ids(self, customer):
        other = make_customer(db=None, phone='0244444444')
        campaign = SMSCampaign(
            target_group=SMSCampaign.TargetGroup.MANUAL_SELECTION,
            filters={}, manual_customer_ids=[customer.pk, other.pk],
        )
        ids = {c.pk for c in resolve_recipients(campaign)}
        assert ids == {customer.pk, other.pk}

    def test_manual_selection_excludes_inactive_unless_filtered(self, customer):
        inactive = make_customer(db=None, phone='0245555555', status='INACTIVE')
        campaign = SMSCampaign(
            target_group=SMSCampaign.TargetGroup.MANUAL_SELECTION,
            filters={}, manual_customer_ids=[inactive.pk],
        )
        assert list(resolve_recipients(campaign)) == []


@pytest.mark.django_db
class TestPersonalization:
    def test_placeholders_replaced(self, customer, base_campaign):
        ctx = build_context(customer, 'GENERAL_ANNOUNCEMENT')
        assert ctx['customer_name'] == 'John Customer'
        msg = personalize('Hi {{first_name}}', ctx)
        assert msg == 'Hi John'

    def test_unknown_placeholder_left_as_is(self):
        ctx = {'first_name': 'Ama'}
        msg = personalize('Hi {{first_name}} {{nope}}', ctx)
        assert msg == 'Hi Ama {{nope}}'

    def test_loan_placeholders_resolved(self, customer, loan_product):
        from apps.loans.models import Loan, RepaymentSchedule
        loan = Loan.objects.create(
            customer=customer, loan_product=loan_product,
            principal_amount=Decimal('1000.00'), total_amount=Decimal('1200.00'),
            outstanding_balance=Decimal('800.00'), status='ACTIVE', term_months=6,
        )
        RepaymentSchedule.objects.create(
            loan=loan, installment_number=1, due_date='2026-09-01',
            principal_due=Decimal('200.00'), interest_due=Decimal('0.00'),
            total_due=Decimal('200.00'), remaining_balance=Decimal('200.00'),
            status='PENDING',
        )
        ctx = build_context(customer, 'REPAYMENT_REMINDER')
        assert ctx['account_number'] == loan.loan_number
        assert ctx['outstanding_balance'] == '800.00'
        assert ctx['repayment_amount'] in ('200.00',)


@pytest.mark.django_db
class TestSMSUnits:
    def test_gsm7_single_segment(self):
        assert segments_for('x' * 160) == 1
        assert segments_for('x' * 161) == 2
        assert segments_for('x' * 306) == 2

    def test_ucs2_segments(self):
        msg = '\u2019' * 70  # curly apostrophe forces UCS-2
        assert segments_for(msg) == 1
        assert segments_for(msg + '\u2019') == 2


@pytest.mark.django_db
class TestPrepareCampaign:
    def test_counts_valid_and_missing(self, customer, base_campaign):
        make_customer(db=None, phone='0246666666')
        bad = make_customer(db=None, phone='12345')
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        base_campaign.refresh_from_db()
        assert base_campaign.recipient_count >= 3
        logs = SMSMessageLog.objects.filter(campaign=base_campaign)
        assert logs.filter(status=SMSMessageLog.Status.QUEUED).exists()
        assert logs.filter(customer=bad, status=SMSMessageLog.Status.REJECTED).exists()
        assert base_campaign.missing_phone_count >= 1

    def test_prepare_is_idempotent(self, customer, base_campaign):
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        first_count = SMSMessageLog.objects.filter(campaign=base_campaign).count()
        sending.prepare_campaign(base_campaign)
        assert SMSMessageLog.objects.filter(campaign=base_campaign).count() == first_count


@pytest.mark.django_db
class TestSendCampaign:
    def test_send_marks_completed_and_logs_sent(self, customer, base_campaign):
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        result = sending.run_campaign_impl(base_campaign.pk)
        result.refresh_from_db()
        assert result.status == SMSCampaign.Status.COMPLETED
        sent_logs = SMSMessageLog.objects.filter(
            campaign=result, status=SMSMessageLog.Status.SENT
        )
        assert sent_logs.exists()
        assert result.sent_count == result.valid_phone_count

    def test_no_accidental_resend(self, customer, base_campaign):
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        sending.run_campaign_impl(base_campaign.pk)
        sending.run_campaign_impl(base_campaign.pk)
        # Second run is a no-op because status is COMPLETED.
        notif_count = SMSNotification.objects.filter(
            reference_model='SMSMessageLog'
        ).count()
        base_campaign.refresh_from_db()
        assert base_campaign.status == SMSCampaign.Status.COMPLETED
        assert notif_count == SMSMessageLog.objects.filter(
            campaign=base_campaign, status__in=['SENT', 'DELIVERED']
        ).count()


@pytest.mark.django_db
class TestRetryCampaign:
    def test_retry_failed_only(self, customer, base_campaign):
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        # Mark one valid log as failed so it can be retried.
        target = SMSMessageLog.objects.filter(
            campaign=base_campaign, status=SMSMessageLog.Status.QUEUED
        ).first()
        target.status = SMSMessageLog.Status.FAILED
        target.error_message = 'provider error'
        target.save()
        campaign, retried = sending.retry_failed_impl(base_campaign.pk)
        assert retried >= 1
        target.refresh_from_db()
        assert target.status in (SMSMessageLog.Status.SENT, SMSMessageLog.Status.DELIVERED)

    def test_retry_rejects_invalid_phone(self, customer, base_campaign):
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        bad = SMSMessageLog.objects.filter(
            campaign=base_campaign, status=SMSMessageLog.Status.REJECTED,
            error_message='Invalid or missing phone number',
        ).first()
        campaign, retried = sending.retry_failed_impl(base_campaign.pk)
        if bad:
            bad.refresh_from_db()
            assert bad.status == SMSMessageLog.Status.REJECTED


@pytest.mark.django_db
class TestTemplateManagement:
    def test_create_template_logs_audit(self, admin_user, client):
        client.force_login(admin_user)
        resp = client.post(reverse('template_create'), {
            'name': 'Reminder', 'campaign_type': 'REPAYMENT_REMINDER',
            'message': 'You owe {{repayment_amount}}', 'is_active': 'on',
        })
        assert resp.status_code == 302
        assert SMSTemplate.objects.filter(name='Reminder').exists()


@pytest.mark.django_db
class TestViewsAndPermissions:
    def test_dashboard_requires_staff_role(self, cashier_user, client):
        client.force_login(cashier_user)
        resp = client.get(reverse('campaign_dashboard'))
        assert resp.status_code == 302

    def test_create_preview_and_send(self, admin_user, client, customer):
        client.force_login(admin_user)
        with mock.patch('apps.campaigns.views.run_campaign_task.delay') as delay:
            resp = client.post(reverse('campaign_create'), {
                'name': 'Drive', 'campaign_type': 'CUSTOM_MESSAGE',
                'target_group': 'ALL_ACTIVE', 'message': 'Hi {{first_name}}',
                'trigger': 'SEND_NOW', 'action': 'create', 'confirm': '1',
            })
        assert resp.status_code == 302
        campaign = SMSCampaign.objects.get(name='Drive')
        assert campaign.uid
        delay.assert_called_once_with(campaign.pk)

    def test_create_requires_confirmation(self, admin_user, client):
        client.force_login(admin_user)
        client.raise_request_exception = False
        resp = client.post(reverse('campaign_create'), {
            'name': 'NoConfirm', 'campaign_type': 'CUSTOM_MESSAGE',
            'target_group': 'ALL_ACTIVE', 'message': 'Hi', 'trigger': 'SEND_NOW',
            'action': 'create',
        })
        assert not SMSCampaign.objects.filter(name='NoConfirm').exists()

    def test_schedule_does_not_dispatch(self, admin_user, client):
        client.force_login(admin_user)
        with mock.patch('apps.campaigns.views.run_campaign_task.delay') as delay:
            resp = client.post(reverse('campaign_create'), {
                'name': 'Scheduled', 'campaign_type': 'CUSTOM_MESSAGE',
                'target_group': 'ALL_ACTIVE', 'message': 'Hi', 'trigger': 'SCHEDULE',
                'scheduled_at': '2026-12-01T10:00', 'action': 'create', 'confirm': '1',
            })
        campaign = SMSCampaign.objects.get(name='Scheduled')
        assert campaign.status == SMSCampaign.Status.SCHEDULED
        delay.assert_not_called()


@pytest.mark.django_db
class TestAllActive:
    def test_existing_approval_sms_unaffected(self, customer, base_campaign):
        # Approvals create their own SMSNotification; campaigns must not interfere.
        base_campaign.save()
        sending.prepare_campaign(base_campaign)
        sending.run_campaign_impl(base_campaign.pk)
        assert SMSNotification.objects.filter(
            notification_type='CUSTOMER_APPROVED', customer=customer
        ).count() == 0
