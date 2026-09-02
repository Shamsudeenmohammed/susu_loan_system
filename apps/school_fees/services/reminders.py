import logging
from decimal import Decimal

from django.utils import timezone

from ..models import (
    ReminderTemplate,
    ReminderLog,
    Student,
    StudentFeeAccount,
)

logger = logging.getLogger('apps.school_fees')

ABBREVIATIONS = {
    'GENERAL': 'GEN',
    'UPCOMING': 'UPCOMING',
    'DUE_DATE': 'DUEDATE',
    'OVERDUE': 'OVERDUE',
    'PARTIAL': 'PARTIAL',
    'PAYMENT_CONFIRMATION': 'PAYCONF',
    'FULLY_PAID': 'FULLPAID',
}


def _format_money(value):
    try:
        return f"{Decimal(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def build_message(template, account):
    """Render an SMS template with real student/parent/balance placeholders."""
    student = account.student
    total = _format_money(account.total_fees)
    paid = _format_money(account.amount_paid)
    balance = _format_money(account.outstanding_balance)
    due = account.due_date.strftime('%d %b %Y') if account.due_date else 'the due date'

    return (template.message
            .replace('{{student_name}}', student.get_full_name())
            .replace('{{parent_name}}', student.parent_name)
            .replace('{{total_fees}}', total)
            .replace('{{amount_paid}}', paid)
            .replace('{{balance}}', balance)
            .replace('{{due_date}}', due))


def _school_name():
    from django.conf import settings
    return getattr(settings, 'SCHOOL_NAME', 'Zemzem Golden Child Academy')


def default_templates():
    """Return the default reminder templates keyed by type."""
    prefix = _school_name() + ": "
    return {
        ReminderTemplate.ReminderType.UPCOMING: (
            prefix + "Dear {{parent_name}}, this is a reminder that {{student_name}} "
            "has an outstanding school fees balance of GHS {{balance}} for the current "
            "term. Kindly make payment before {{due_date}}. Thank you."
        ),
        ReminderTemplate.ReminderType.DUE_DATE: (
            prefix + "Dear {{parent_name}}, school fees for {{student_name}} are due "
            "on {{due_date}}. Outstanding balance: GHS {{balance}}. Kindly pay to avoid "
            "penalties. Thank you."
        ),
        ReminderTemplate.ReminderType.OVERDUE: (
            prefix + "Dear {{parent_name}}, the school fees for {{student_name}} are "
            "now OVERDUE. Outstanding balance: GHS {{balance}}. Please settle payment "
            "immediately. Thank you."
        ),
        ReminderTemplate.ReminderType.PARTIAL: (
            prefix + "Dear {{parent_name}}, we have received a partial payment towards "
            "the school fees for {{student_name}}. Total fees: GHS {{total_fees}}, Paid: "
            "GHS {{amount_paid}}, Remaining: GHS {{balance}}. Thank you."
        ),
        ReminderTemplate.ReminderType.PAYMENT_CONFIRMATION: (
            prefix + "Dear {{parent_name}}, we have received your payment for "
            "{{student_name}}. Amount paid: GHS {{amount_paid}}. Remaining balance: GHS "
            "{{balance}}. Thank you."
        ),
        ReminderTemplate.ReminderType.FULLY_PAID: (
            prefix + "Dear {{parent_name}}, this is to confirm that the school fees "
            "for {{student_name}} have been FULLY PAID (GHS {{total_fees}}). Thank you "
            "for your prompt payment."
        ),
    }


def ensure_templates():
    """Create any missing default reminder templates."""
    defaults = default_templates()
    created = []
    for rtype, message in defaults.items():
        _, was_created = ReminderTemplate.objects.get_or_create(
            reminder_type=rtype,
            defaults={'name': dict(ReminderTemplate.ReminderType.choices)[rtype], 'message': message},
        )
        if was_created:
            created.append(rtype)
    return created


def get_template(reminder_type):
    ensure_templates()
    return ReminderTemplate.objects.get(reminder_type=reminder_type)


def _unique_key(reminder_type, student_id, account_id, date):
    return f'feerem:{ABBREVIATIONS.get(reminder_type, "GEN")}:{student_id}:{account_id}:{date}'


def _send_one(service, account, reminder_type, sent_by, unique_key, student_pk=None):
    """Render and send an SMS for a single fee account."""
    template = get_template(reminder_type)
    message = build_message(template, account)
    phone = account.student.parent_phone

    try:
        from apps.notifications.services.sms import get_sms_service
        from apps.notifications.models import SMSNotification
        svc = service or get_sms_service()
        notification = svc.send_sms(
            phone_number=phone,
            message=message,
            notification_type='FEE_REMINDER',
            customer=None,
            reference_model='StudentFeeAccount',
            reference_id=account.pk,
            unique_key=unique_key,
        )
        log_status = ReminderLog.Status.SENT
        if notification and getattr(notification, 'status', '') == SMSNotification.Status.FAILED:
            log_status = ReminderLog.Status.FAILED
        return ReminderLog.objects.create(
            reminder_type=reminder_type,
            student_id=student_pk or account.student_id,
            parent_phone=phone or '',
            message=message,
            status=log_status,
            unique_key=unique_key,
            sent_by=sent_by,
        )
    except Exception as e:
        logger.exception(f"Fee reminder SMS failed for {account.pk}: {e}")
        return ReminderLog.objects.create(
            reminder_type=reminder_type,
            student_id=student_pk or account.student_id,
            parent_phone=phone or '',
            message=message,
            status=ReminderLog.Status.FAILED,
            unique_key=unique_key,
            sent_by=sent_by,
        )


def send_reminder_to_student(student, reminder_type, sent_by=None, service=None):
    """Send a reminder for a specific student (all their open accounts)."""
    today = timezone.now().date()
    sent = []
    for account in student.fee_accounts.all():
        unique_key = _unique_key(reminder_type, student.pk, account.pk, today)
        log = _send_one(service, account, reminder_type, sent_by, unique_key, student.pk)
        sent.append(log)
    return sent


def send_reminders_for_accounts(accounts, reminder_type, sent_by=None, service=None):
    """Send reminders to a queryset of fee accounts. Returns sent logs."""
    today = timezone.now().date()
    sent = []
    for account in accounts:
        unique_key = _unique_key(reminder_type, account.student_id, account.pk, today)
        log = _send_one(service, account, reminder_type, sent_by, unique_key)
        sent.append(log)
    return sent


def send_payment_confirmation(account, payment, service=None):
    """Send payment confirmation SMS after an online/recorded payment."""
    from django.utils import timezone as tz
    today = tz.now().date()
    unique_key = f'feerem:PAYCONF:{payment.pk}:{today}'
    return _send_one(service, account, ReminderTemplate.ReminderType.PAYMENT_CONFIRMATION,
                     payment.recorded_by, unique_key)


def send_fully_paid_notification(account, payment=None, service=None):
    """Send a fully-paid notification when an account reaches full payment."""
    from django.utils import timezone as tz
    today = tz.now().date()
    unique_key = f'feerem:FULLPAID:{account.pk}:{today}'
    sent_by = payment.recorded_by if payment else None
    return _send_one(service, account, ReminderTemplate.ReminderType.FULLY_PAID, sent_by, unique_key)
