import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.notifications.services import messages as message_templates
from apps.notifications.services.sms import get_sms_service
from .models import Customer

logger = logging.getLogger('apps.customers')


class ApprovalResult:
    """Outcome of an approval/rejection operation."""

    def __init__(self, changed, sms_sent, sms_message, reason=None):
        self.changed = changed
        self.sms_sent = sms_sent
        self.sms_message = sms_message
        self.reason = reason


def approve_customer(customer, actor=None, ip_address=None):
    """
    Approve a pending customer.

    - Only transitions when the customer is currently PENDING (idempotent for
      already-approved customers, so duplicate approval does NOT re-send SMS).
    - Uses an atomic transaction for the status update.
    - Sends the approval SMS AFTER the status update is committed so a temporary
      SMS-provider failure never corrupts the customer's approval state.
    - Returns an ApprovalResult describing what happened.
    """
    customer = Customer.objects.select_for_update().get(pk=customer.pk)

    if customer.status != Customer.Status.PENDING:
        existing_sms = customer.sms_notifications.filter(
            notification_type='CUSTOMER_APPROVED'
        ).first()
        return ApprovalResult(
            changed=False,
            sms_sent=existing_sms is not None,
            sms_message=(
                'Customer was already approved; no action taken.'
                if existing_sms is None
                else f'Customer was already approved. SMS already sent ({existing_sms.notification_number}).'
            ),
        )

    with transaction.atomic():
        customer.status = Customer.Status.ACTIVE
        customer.approved_at = timezone.now()
        customer.approved_by = actor
        customer.rejected_at = None
        customer.rejected_by = None
        customer.rejection_reason = ''
        customer.save(update_fields=[
            'status', 'approved_at', 'approved_by',
            'rejected_at', 'rejected_by', 'rejection_reason', 'updated_at',
        ])

        AuditLog.log(
            action=AuditLog.ActionType.CUSTOMER_APPROVED,
            description=(
                f"Customer {customer.customer_number} ({customer.get_full_name()}) "
                f"approved and activated."
            ),
            user=actor,
            object_type='Customer',
            object_id=customer.pk,
            ip_address=ip_address,
        )

    sms_sent = _send_approval_sms(customer)
    return ApprovalResult(
        changed=True,
        sms_sent=sms_sent,
        sms_message=(
            f'Customer {customer.customer_number} has been approved successfully. '
            'SMS notification sent.'
            if sms_sent
            else f'Customer {customer.customer_number} was approved, but the SMS notification could not be delivered.'
        ),
    )


def reject_customer(customer, actor=None, ip_address=None, reason=''):
    """
    Reject a pending customer (sets status to REJECTED rather than deleting).

    Idempotent: rejecting an already-rejected/non-pending customer is a no-op.
    """
    customer = Customer.objects.select_for_update().get(pk=customer.pk)

    if customer.status != Customer.Status.PENDING:
        return ApprovalResult(
            changed=False,
            sms_sent=False,
            sms_message='Customer is not pending approval; no action taken.',
        )

    with transaction.atomic():
        customer.status = Customer.Status.REJECTED
        customer.rejected_at = timezone.now()
        customer.rejected_by = actor
        customer.rejection_reason = reason or ''
        customer.approved_at = None
        customer.approved_by = None
        customer.save(update_fields=[
            'status', 'rejected_at', 'rejected_by', 'rejection_reason',
            'approved_at', 'approved_by', 'updated_at',
        ])

        AuditLog.log(
            action=AuditLog.ActionType.CUSTOMER_REJECTED,
            description=(
                f"Customer {customer.customer_number} ({customer.get_full_name()}) "
                f"registration rejected. Reason: {reason or 'Not provided'}"
            ),
            user=actor,
            object_type='Customer',
            object_id=customer.pk,
            ip_address=ip_address,
        )

    sms_sent = _send_rejection_sms(customer)
    return ApprovalResult(
        changed=True,
        sms_sent=sms_sent,
        sms_message=(
            f'Customer {customer.customer_number} has been rejected.'
            if sms_sent
            else f'Customer {customer.customer_number} was rejected, but the SMS notification could not be delivered.'
        ),
    )


def _send_approval_sms(customer):
    """Send + log the approval SMS. Uses unique_key for duplicate protection."""
    phone = customer.phone
    if not phone:
        return False
    try:
        service = get_sms_service()
        notification = service.send_sms(
            phone_number=phone,
            message=message_templates.customer_approved(customer.get_full_name()),
            notification_type='CUSTOMER_APPROVED',
            customer=customer,
            reference_model='Customer',
            reference_id=customer.pk,
            unique_key=f'customer_approved:{customer.pk}',
        )
        return notification.status in (
            'SENT', 'DELIVERED',
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Approval SMS failed for customer %s", customer.pk)
        _audit_sms_failure(customer, 'CUSTOMER_APPROVED', str(exc))
        return False


def _send_rejection_sms(customer):
    phone = customer.phone
    if not phone:
        return False
    try:
        service = get_sms_service()
        notification = service.send_sms(
            phone_number=phone,
            message=message_templates.customer_rejected(
                customer.get_full_name(), customer.rejection_reason
            ),
            notification_type='CUSTOMER_REJECTED',
            customer=customer,
            reference_model='Customer',
            reference_id=customer.pk,
            unique_key=f'customer_rejected:{customer.pk}',
        )
        return notification.status in ('SENT', 'DELIVERED')
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Rejection SMS failed for customer %s", customer.pk)
        return False


def _audit_sms_failure(customer, action, error):
    from apps.notifications.models import SMSNotification
    try:
        SMSNotification.objects.create(
            customer=customer,
            phone_number=customer.phone,
            message=message_templates.customer_approved(customer.get_full_name()),
            notification_type=action,
            reference_model='Customer',
            reference_id=customer.pk,
            status=SMSNotification.Status.FAILED,
            error_message=error[:500],
        )
    except Exception:  # pragma: no cover - defensive
        pass
