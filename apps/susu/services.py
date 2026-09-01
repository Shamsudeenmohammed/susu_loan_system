import logging

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.notifications.services import messages as message_templates
from apps.notifications.services.sms import get_sms_service

logger = logging.getLogger('apps.susu')


class ActivationResult:
    """Outcome of a Susu account activation operation."""

    def __init__(self, changed, sms_sent, sms_message):
        self.changed = changed
        self.sms_sent = sms_sent
        self.sms_message = sms_message


def activate_susu_account(account, actor=None, ip_address=None):
    """
    Activate a Susu/Savings account (transition INTO the active state).

    - Only sends the activation SMS when the account actually transitions from a
      non-active state to ACTIVE (idempotent for already-active accounts, so
      re-saving does NOT trigger duplicate SMS).
    - Status update is atomic.
    - SMS is sent after the status is committed so a provider failure never
      corrupts the account state.
    """
    account = type(account).objects.select_for_update().get(pk=account.pk)

    if account.status == account.Status.ACTIVE:
        existing_sms = account.customer.sms_notifications.filter(
            notification_type='SUSU_ACTIVATED'
        ).first()
        return ActivationResult(
            changed=False,
            sms_sent=existing_sms is not None,
            sms_message=(
                f'Account {account.account_number} is already active; no activation or SMS sent.'
                if existing_sms is None
                else f'Account {account.account_number} is already active. Activation SMS already sent.'
            ),
        )

    with transaction.atomic():
        account.status = account.Status.ACTIVE
        account.activated_at = timezone.now()
        account.save(update_fields=['status', 'activated_at', 'updated_at'])

        AuditLog.log(
            action=AuditLog.ActionType.SUSU_ACCOUNT_ACTIVATED,
            description=(
                f"Susu account {account.account_number} activated for "
                f"customer {account.customer.customer_number}."
            ),
            user=actor,
            object_type='SusuAccount',
            object_id=account.pk,
            ip_address=ip_address,
        )

    sms_sent = _send_activation_sms(account)
    return ActivationResult(
        changed=True,
        sms_sent=sms_sent,
        sms_message=(
            f'Account {account.account_number} has been activated. SMS notification sent.'
            if sms_sent
            else f'Account {account.account_number} was activated, but the SMS notification could not be delivered.'
        ),
    )


def activate_customer_susu_accounts(customer, actor=None, ip_address=None):
    """
    Activate all non-active, non-closed Susu accounts for a customer.
    Returns the first meaningful ActivationResult (or a no-change result).
    """
    from .models import SusuAccount
    accounts = SusuAccount.objects.filter(
        customer=customer,
    ).exclude(status=SusuAccount.Status.CLOSED)
    result = None
    for account in accounts:
        result = activate_susu_account(account, actor=actor, ip_address=ip_address)
    return result or ActivationResult(
        changed=False,
        sms_sent=False,
        sms_message='No inactive Susu accounts found to activate.',
    )


def _send_activation_sms(account):
    customer = account.customer
    phone = customer.phone
    if not phone:
        return False
    try:
        service = get_sms_service()
        notification = service.send_sms(
            phone_number=phone,
            message=message_templates.susu_account_activated(
                customer.get_full_name(), account.account_number
            ),
            notification_type='SUSU_ACTIVATED',
            customer=customer,
            reference_model='SusuAccount',
            reference_id=account.pk,
            unique_key=f'susu_activated:{account.pk}',
        )
        return notification.status in ('SENT', 'DELIVERED')
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Susu activation SMS failed for account %s", account.pk)
        return False
