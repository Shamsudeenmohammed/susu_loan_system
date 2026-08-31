from celery import shared_task
import logging

from apps.notifications.services import messages as templates

logger = logging.getLogger('apps.notifications')


@shared_task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def send_sms_task(self, phone_number, message, notification_type='GENERAL',
                  customer_id=None, reference_model='', reference_id=None,
                  unique_key=''):
    """
    Celery task for sending a single SMS asynchronously.

    The actual SMS dispatch is delegated to the SMS service, which handles
    the provider, logging and duplicat e protection. This task adds retry
    semantics on top for transient failures.
    """
    from apps.notifications.services.sms import get_sms_service
    from apps.customers.models import Customer

    customer = None
    if customer_id:
        try:
            customer = Customer.objects.get(pk=customer_id)
        except Customer.DoesNotExist:
            pass

    try:
        service = get_sms_service()
        notification = service.send_sms(
            phone_number=phone_number,
            message=message,
            notification_type=notification_type,
            customer=customer,
            reference_model=reference_model,
            reference_id=reference_id,
            unique_key=unique_key,
        )
        return {'status': notification.status, 'notification_id': notification.pk}
    except Exception as exc:
        logger.exception(f"SMS task failed: {exc}")
        self.retry(exc=exc)


@shared_task
def send_contribution_sms(transaction_pk):
    """Send SMS for a contribution transaction."""
    from apps.payments.models import Transaction
    from apps.notifications.services.sms import get_sms_service

    try:
        txn = Transaction.objects.select_related('customer').get(pk=transaction_pk)
        phone = txn.customer.phone
        if not phone:
            return

        balance = txn.balance_after
        msg = templates.contribution_received(txn.amount, balance, txn.transaction_number)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='CONTRIBUTION',
            customer=txn.customer,
            reference_model='Transaction',
            reference_id=txn.pk,
            unique_key=f'contribution:{txn.pk}',
        )
    except Exception as e:
        logger.exception(f"Contribution SMS failed: {e}")


@shared_task
def send_withdrawal_request_sms(withdrawal_pk):
    """Send SMS for withdrawal request."""
    from apps.payments.models import Withdrawal
    from apps.notifications.services.sms import get_sms_service

    try:
        w = Withdrawal.objects.select_related('customer').get(pk=withdrawal_pk)
        phone = w.customer.phone
        if not phone:
            return

        msg = templates.withdrawal_request(w.withdrawal_number, w.amount)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='WITHDRAWAL_REQUEST',
            customer=w.customer,
            reference_model='Withdrawal',
            reference_id=w.pk,
            unique_key=f'withdrawal_request:{w.pk}',
        )
    except Exception as e:
        logger.exception(f"Withdrawal request SMS failed: {e}")


@shared_task
def send_withdrawal_status_sms(withdrawal_pk, status):
    """Send SMS for withdrawal approval/rejection/completion."""
    from apps.payments.models import Withdrawal
    from apps.notifications.services.sms import get_sms_service

    try:
        w = Withdrawal.objects.select_related('customer').get(pk=withdrawal_pk)
        phone = w.customer.phone
        if not phone:
            return

        if status == 'APPROVED':
            msg = templates.withdrawal_approved(w.withdrawal_number, w.amount)
            ntype = 'WITHDRAWAL_APPROVED'
            ukey = f'withdrawal_status:{w.pk}:APPROVED'
        elif status == 'REJECTED':
            msg = templates.withdrawal_rejected(w.withdrawal_number, w.amount)
            ntype = 'WITHDRAWAL_REJECTED'
            ukey = f'withdrawal_status:{w.pk}:REJECTED'
        else:
            return

        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type=ntype,
            customer=w.customer,
            reference_model='Withdrawal',
            reference_id=w.pk,
            unique_key=ukey,
        )
    except Exception as e:
        logger.exception(f"Withdrawal status SMS failed: {e}")


@shared_task
def send_loan_application_sms(loan_pk):
    """Send SMS for loan application."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import get_sms_service

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = templates.loan_application_submitted(loan.loan_number, loan.principal_amount)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='LOAN_APPLICATION',
            customer=loan.customer,
            reference_model='Loan',
            reference_id=loan.pk,
            unique_key=f'loan_application:{loan.pk}',
        )
    except Exception as e:
        logger.exception(f"Loan application SMS failed: {e}")


@shared_task
def send_loan_approved_sms(loan_pk):
    """Send SMS for loan approval."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import get_sms_service

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = templates.loan_approved(loan.loan_number, loan.principal_amount)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='LOAN_APPROVED',
            customer=loan.customer,
            reference_model='Loan',
            reference_id=loan.pk,
            unique_key=f'loan_approved:{loan.pk}',
        )
    except Exception as e:
        logger.exception(f"Loan approved SMS failed: {e}")


@shared_task
def send_loan_rejected_sms(loan_pk):
    """Send SMS for loan rejection."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import get_sms_service

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        reason = loan.rejection_reason or 'Please contact us for details.'
        msg = templates.loan_rejected(loan.loan_number, loan.principal_amount, reason)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='LOAN_REJECTED',
            customer=loan.customer,
            reference_model='Loan',
            reference_id=loan.pk,
            unique_key=f'loan_rejected:{loan.pk}',
        )
    except Exception as e:
        logger.exception(f"Loan rejected SMS failed: {e}")


@shared_task
def send_loan_disbursement_sms(loan_pk):
    """Send SMS for loan disbursement."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import get_sms_service

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = templates.loan_disbursed(loan.loan_number, loan.disbursement_amount or loan.principal_amount)
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='LOAN_DISBURSEMENT',
            customer=loan.customer,
            reference_model='Loan',
            reference_id=loan.pk,
            unique_key=f'loan_disbursement:{loan.pk}',
        )
    except Exception as e:
        logger.exception(f"Loan disbursement SMS failed: {e}")


@shared_task
def send_repayment_sms(repayment_pk):
    """Send SMS for loan repayment."""
    from apps.loans.models import LoanRepayment
    from apps.notifications.services.sms import get_sms_service

    try:
        r = LoanRepayment.objects.select_related('loan', 'loan__customer').get(pk=repayment_pk)
        phone = r.loan.customer.phone
        if not phone:
            return

        msg = templates.loan_repayment_received(
            r.amount, r.loan.loan_number, r.loan.outstanding_balance
        )
        get_sms_service().send_sms(
            phone_number=phone,
            message=msg,
            notification_type='LOAN_REPAYMENT',
            customer=r.loan.customer,
            reference_model='LoanRepayment',
            reference_id=r.pk,
            unique_key=f'repayment:{r.pk}',
        )
    except Exception as e:
        logger.exception(f"Repayment SMS failed: {e}")


@shared_task
def send_repayment_reminders():
    """Scheduled task to send repayment reminders for upcoming and overdue payments."""
    from django.utils import timezone
    from apps.loans.models import RepaymentSchedule
    from apps.notifications.services.sms import get_sms_service

    today = timezone.now().date()

    service = get_sms_service()

    due_today = RepaymentSchedule.objects.filter(
        due_date=today,
        status__in=['PENDING', 'PARTIALLY_PAID']
    ).select_related('loan', 'loan__customer')

    for schedule in due_today:
        phone = schedule.loan.customer.phone
        if phone:
            amount = schedule.total_due - schedule.amount_paid
            msg = templates.repayment_reminder(amount, schedule.loan.loan_number)
            service.send_sms(
                phone_number=phone,
                message=msg,
                notification_type='REPAYMENT_REMINDER',
                customer=schedule.loan.customer,
                reference_model='RepaymentSchedule',
                reference_id=schedule.pk,
                unique_key=f'repayment_reminder:{schedule.pk}:{today}',
            )

    overdue = RepaymentSchedule.objects.filter(
        due_date__lt=today,
        status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue']
    ).select_related('loan', 'loan__customer')

    for schedule in overdue:
        phone = schedule.loan.customer.phone
        if phone:
            amount = schedule.total_due - schedule.amount_paid
            msg = templates.repayment_overdue(amount, schedule.loan.loan_number, schedule.due_date)
            service.send_sms(
                phone_number=phone,
                message=msg,
                notification_type='REPAYMENT_REMINDER',
                customer=schedule.loan.customer,
                reference_model='RepaymentSchedule',
                reference_id=schedule.pk,
                unique_key=f'repayment_overdue:{schedule.pk}:{schedule.due_date}',
            )
