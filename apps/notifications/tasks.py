from celery import shared_task
import logging

logger = logging.getLogger('apps.notifications')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_sms_task(self, phone_number, message, notification_type='GENERAL',
                  customer_id=None, reference_model='', reference_id=None):
    """Celery task for sending SMS asynchronously."""
    from apps.notifications.services.sms import send_sms
    from apps.customers.models import Customer

    customer = None
    if customer_id:
        try:
            customer = Customer.objects.get(pk=customer_id)
        except Customer.DoesNotExist:
            pass

    try:
        notification = send_sms(
            phone_number=phone_number,
            message=message,
            notification_type=notification_type,
            customer=customer,
            reference_model=reference_model,
            reference_id=reference_id,
        )
        return {'status': notification.status, 'notification_id': notification.pk}
    except Exception as exc:
        logger.exception(f"SMS task failed: {exc}")
        self.retry(exc=exc)


@shared_task
def send_contribution_sms(transaction_pk):
    """Send SMS for a contribution transaction."""
    from apps.payments.models import Transaction
    from apps.notifications.services.sms import send_sms

    try:
        txn = Transaction.objects.select_related('customer').get(pk=transaction_pk)
        phone = txn.customer.phone
        if not phone:
            return

        balance = txn.balance_after
        msg = (
            f"Zemzem Savings and Loans: Your contribution of GHS {txn.amount:.2f} has been received successfully. "
            f"Your new balance is GHS {balance:.2f}. "
            f"Transaction: {txn.transaction_number}."
        )
        send_sms(
            phone_number=phone,
            message=msg,
            notification_type='CONTRIBUTION',
            customer=txn.customer,
            reference_model='Transaction',
            reference_id=txn.pk,
        )
    except Exception as e:
        logger.exception(f"Contribution SMS failed: {e}")


@shared_task
def send_withdrawal_request_sms(withdrawal_pk):
    """Send SMS for withdrawal request."""
    from apps.payments.models import Withdrawal
    from apps.notifications.services.sms import send_sms

    try:
        w = Withdrawal.objects.select_related('customer').get(pk=withdrawal_pk)
        phone = w.customer.phone
        if not phone:
            return

        msg = (
            f"Zemzem Savings and Loans: Your withdrawal request {w.withdrawal_number} for GHS {w.amount:.2f} "
            f"has been submitted and is under review."
        )
        send_sms(
            phone_number=phone,
            message=msg,
            notification_type='WITHDRAWAL_REQUEST',
            customer=w.customer,
            reference_model='Withdrawal',
            reference_id=w.pk,
        )
    except Exception as e:
        logger.exception(f"Withdrawal request SMS failed: {e}")


@shared_task
def send_withdrawal_status_sms(withdrawal_pk, status):
    """Send SMS for withdrawal approval/rejection/completion."""
    from apps.payments.models import Withdrawal
    from apps.notifications.services.sms import send_sms

    try:
        w = Withdrawal.objects.select_related('customer').get(pk=withdrawal_pk)
        phone = w.customer.phone
        if not phone:
            return

        if status == 'APPROVED':
            msg = f"Zemzem Savings and Loans: Your withdrawal request {w.withdrawal_number} for GHS {w.amount:.2f} has been approved and processed."
            ntype = 'WITHDRAWAL_APPROVED'
        elif status == 'REJECTED':
            msg = f"Zemzem Savings and Loans: Your withdrawal request {w.withdrawal_number} for GHS {w.amount:.2f} has been rejected. Please contact us for details."
            ntype = 'WITHDRAWAL_REJECTED'
        else:
            return

        send_sms(phone, msg, ntype, w.customer, 'Withdrawal', w.pk)
    except Exception as e:
        logger.exception(f"Withdrawal status SMS failed: {e}")


@shared_task
def send_loan_application_sms(loan_pk):
    """Send SMS for loan application."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import send_sms

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = (
            f"Zemzem Savings and Loans: Your loan application {loan.loan_number} for GHS {loan.principal_amount:.2f} "
            f"has been received and is awaiting review."
        )
        send_sms(phone, msg, 'LOAN_APPLICATION', loan.customer, 'Loan', loan.pk)
    except Exception as e:
        logger.exception(f"Loan application SMS failed: {e}")


@shared_task
def send_loan_approved_sms(loan_pk):
    """Send SMS for loan approval."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import send_sms

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = (
            f"Zemzem Savings and Loans: Congratulations. Your loan application {loan.loan_number} for GHS {loan.principal_amount:.2f} "
            f"has been approved. Please check your account for repayment details."
        )
        send_sms(phone, msg, 'LOAN_APPROVED', loan.customer, 'Loan', loan.pk)
    except Exception as e:
        logger.exception(f"Loan approved SMS failed: {e}")


@shared_task
def send_loan_disbursement_sms(loan_pk):
    """Send SMS for loan disbursement."""
    from apps.loans.models import Loan
    from apps.notifications.services.sms import send_sms

    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
        phone = loan.customer.phone
        if not phone:
            return

        msg = (
            f"Zemzem Savings and Loans: Your loan {loan.loan_number} of GHS {loan.disbursement_amount:.2f} "
            f"has been disbursed to your account. Your repayment schedule is now active."
        )
        send_sms(phone, msg, 'LOAN_DISBURSEMENT', loan.customer, 'Loan', loan.pk)
    except Exception as e:
        logger.exception(f"Loan disbursement SMS failed: {e}")


@shared_task
def send_repayment_sms(repayment_pk):
    """Send SMS for loan repayment."""
    from apps.loans.models import LoanRepayment
    from apps.notifications.services.sms import send_sms

    try:
        r = LoanRepayment.objects.select_related('loan', 'loan__customer').get(pk=repayment_pk)
        phone = r.loan.customer.phone
        if not phone:
            return

        msg = (
            f"Zemzem Savings and Loans: Loan repayment of GHS {r.amount:.2f} received for loan {r.loan.loan_number}. "
            f"Outstanding balance: GHS {r.loan.outstanding_balance:.2f}."
        )
        send_sms(phone, msg, 'LOAN_REPAYMENT', r.loan.customer, 'LoanRepayment', r.pk)
    except Exception as e:
        logger.exception(f"Repayment SMS failed: {e}")


@shared_task
def send_repayment_reminders():
    """Scheduled task to send repayment reminders for upcoming and overdue payments."""
    from django.utils import timezone
    from apps.loans.models import RepaymentSchedule, Loan
    from apps.notifications.services.sms import send_sms

    today = timezone.now().date()
    upcoming = today + timezone.timedelta(days=3)

    # Due today
    due_today = RepaymentSchedule.objects.filter(
        due_date=today,
        status__in=['PENDING', 'PARTIALLY_PAID']
    ).select_related('loan', 'loan__customer')

    for schedule in due_today:
        phone = schedule.loan.customer.phone
        if phone:
            amount = schedule.total_due - schedule.amount_paid
            msg = (
                f"Zemzem Savings and Loans Reminder: You have a loan repayment of GHS {amount:.2f} "
                f"due today for loan {schedule.loan.loan_number}. "
                f"Please make your payment to avoid penalties."
            )
            send_sms(phone, msg, 'REPAYMENT_REMINDER', schedule.loan.customer, 'RepaymentSchedule', schedule.pk)

    # Overdue
    overdue = RepaymentSchedule.objects.filter(
        due_date__lt=today,
        status__in=['PENDING', 'PARTIALLY_PAID', 'Overdue']
    ).select_related('loan', 'loan__customer')

    for schedule in overdue:
        phone = schedule.loan.customer.phone
        if phone:
            amount = schedule.total_due - schedule.amount_paid
            msg = (
                f"Zemzem Savings and Loans OVERDUE: Your loan repayment of GHS {amount:.2f} for loan "
                f"{schedule.loan.loan_number} was due on {schedule.due_date}. "
                f"Please pay immediately to avoid additional penalties."
            )
            send_sms(phone, msg, 'REPAYMENT_REMINDER', schedule.loan.customer, 'RepaymentSchedule', schedule.pk)
