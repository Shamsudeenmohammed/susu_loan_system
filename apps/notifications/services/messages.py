"""
Centralized SMS message generation for Zemzem Savings and Loans.

All notification text is generated here rather than hard-coded inside views
or Celery tasks. This keeps messages consistent and easy to maintain.
"""

BRAND = 'Zemzem Savings and Loans'


def contribution_received(amount, new_balance, reference):
    return (
        f"{BRAND}: Your contribution of GHS {amount:.2f} has been received successfully. "
        f"Your new savings balance is GHS {new_balance:,.2f}. Ref: {reference}."
    )


def withdrawal_request(withdrawal_number, amount):
    return (
        f"{BRAND}: Your withdrawal request {withdrawal_number} for GHS {amount:.2f} "
        f"has been submitted and is under review."
    )


def withdrawal_approved(withdrawal_number, amount):
    return (
        f"{BRAND}: Your withdrawal request {withdrawal_number} for GHS {amount:.2f} "
        f"has been approved and processed."
    )


def withdrawal_rejected(withdrawal_number, amount):
    return (
        f"{BRAND}: Your withdrawal request {withdrawal_number} for GHS {amount:.2f} "
        f"has been rejected. Please contact us for details."
    )


def loan_application_submitted(loan_number, amount):
    return (
        f"{BRAND}: Your loan application {loan_number} for GHS {amount:.2f} "
        f"has been received and is awaiting review."
    )


def loan_approved(loan_number, amount):
    return (
        f"{BRAND}: Congratulations. Your loan application {loan_number} for GHS {amount:.2f} "
        f"has been approved. Please check your account for repayment details."
    )


def loan_rejected(loan_number, amount, reason=None):
    reason = reason or 'Please contact us for details.'
    return (
        f"{BRAND}: We regret to inform you that your loan application "
        f"{loan_number} for GHS {amount:.2f} was not approved. {reason}"
    )


def loan_disbursed(loan_number, amount):
    return (
        f"{BRAND}: Your loan {loan_number} of GHS {amount:.2f} "
        f"has been disbursed to your account. Your repayment schedule is now active."
    )


def loan_repayment_received(amount, loan_number, outstanding_balance):
    return (
        f"{BRAND}: Loan repayment of GHS {amount:.2f} received for loan {loan_number}. "
        f"Outstanding balance: GHS {outstanding_balance:.2f}."
    )


def repayment_reminder(amount, loan_number):
    return (
        f"{BRAND} Reminder: You have a loan repayment of GHS {amount:.2f} "
        f"due today for loan {loan_number}. Please make your payment to avoid penalties."
    )


def repayment_overdue(amount, loan_number, due_date):
    return (
        f"{BRAND} OVERDUE: Your loan repayment of GHS {amount:.2f} for loan "
        f"{loan_number} was due on {due_date}. Please pay immediately to avoid additional penalties."
    )


def otp_verification(code):
    return (
        f"{BRAND}: Your verification code is {code}. Do not share this code with anyone."
    )
