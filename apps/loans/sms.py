import logging

from apps.notifications.services import messages as templates
from apps.notifications.services.sms import get_sms_service

logger = logging.getLogger('apps.loans')


def _send(notification_type, reference_model, reference_id, phone_number,
          message, customer, unique_key):
    """Dispatch one transactional SMS synchronously, mirroring the school
    fees module: send in the request, never raise, only log failures."""
    if not phone_number:
        logger.info(f"[SMS] Skipping {notification_type}: no phone number "
                    f"on {reference_model} {reference_id}")
        return None
    try:
        return get_sms_service().send_sms(
            phone_number=phone_number,
            message=message,
            notification_type=notification_type,
            customer=customer,
            reference_model=reference_model,
            reference_id=reference_id,
            unique_key=unique_key,
        )
    except Exception as exc:
        logger.exception(f"{notification_type} SMS failed for "
                         f"{reference_model} {reference_id}: {exc}")
        return None


def send_loan_application_sms(loan_pk):
    """Send the SMS a customer gets when their loan application is submitted."""
    from apps.loans.models import Loan
    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
    except Loan.DoesNotExist:
        logger.warning(f"Loan {loan_pk} not found; application SMS skipped")
        return None
    msg = templates.loan_application_submitted(loan.loan_number, loan.principal_amount)
    return _send(
        notification_type='LOAN_APPLICATION',
        reference_model='Loan',
        reference_id=loan.pk,
        phone_number=loan.customer.phone,
        message=msg,
        customer=loan.customer,
        unique_key=f'loan_application:{loan.pk}',
    )


def send_loan_approved_sms(loan_pk):
    """Send the SMS a customer gets when their loan is approved."""
    from apps.loans.models import Loan
    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
    except Loan.DoesNotExist:
        logger.warning(f"Loan {loan_pk} not found; approval SMS skipped")
        return None
    msg = templates.loan_approved(loan.loan_number, loan.principal_amount)
    return _send(
        notification_type='LOAN_APPROVED',
        reference_model='Loan',
        reference_id=loan.pk,
        phone_number=loan.customer.phone,
        message=msg,
        customer=loan.customer,
        unique_key=f'loan_approved:{loan.pk}',
    )


def send_loan_rejected_sms(loan_pk):
    """Send the SMS a customer gets when their loan is rejected."""
    from apps.loans.models import Loan
    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
    except Loan.DoesNotExist:
        logger.warning(f"Loan {loan_pk} not found; rejection SMS skipped")
        return None
    reason = loan.rejection_reason or 'Please contact us for details.'
    msg = templates.loan_rejected(loan.loan_number, loan.principal_amount, reason)
    return _send(
        notification_type='LOAN_REJECTED',
        reference_model='Loan',
        reference_id=loan.pk,
        phone_number=loan.customer.phone,
        message=msg,
        customer=loan.customer,
        unique_key=f'loan_rejected:{loan.pk}',
    )


def send_loan_disbursement_sms(loan_pk):
    """Send the SMS a customer gets when their loan is disbursed."""
    from apps.loans.models import Loan
    try:
        loan = Loan.objects.select_related('customer').get(pk=loan_pk)
    except Loan.DoesNotExist:
        logger.warning(f"Loan {loan_pk} not found; disbursement SMS skipped")
        return None
    msg = templates.loan_disbursed(
        loan.loan_number, loan.disbursement_amount or loan.principal_amount
    )
    return _send(
        notification_type='LOAN_DISBURSEMENT',
        reference_model='Loan',
        reference_id=loan.pk,
        phone_number=loan.customer.phone,
        message=msg,
        customer=loan.customer,
        unique_key=f'loan_disbursement:{loan.pk}',
    )


def send_repayment_sms(repayment_pk):
    """Send the SMS a customer gets when a loan repayment is recorded."""
    from apps.loans.models import LoanRepayment
    try:
        repayment = LoanRepayment.objects.select_related('loan', 'loan__customer').get(pk=repayment_pk)
    except LoanRepayment.DoesNotExist:
        logger.warning(f"LoanRepayment {repayment_pk} not found; repayment SMS skipped")
        return None
    msg = templates.loan_repayment_received(
        repayment.amount, repayment.loan.loan_number, repayment.loan.outstanding_balance
    )
    return _send(
        notification_type='LOAN_REPAYMENT',
        reference_model='LoanRepayment',
        reference_id=repayment.pk,
        phone_number=repayment.loan.customer.phone,
        message=msg,
        customer=repayment.loan.customer,
        unique_key=f'repayment:{repayment.pk}',
    )