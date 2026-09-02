import logging
from decimal import Decimal

from django.db.models import Sum

from ..models import (
    FeeStructure,
    StudentFeeAccount,
    AcademicYear,
    Term,
)

logger = logging.getLogger('apps.school_fees')


def total_fees_for(student, term):
    """Sum all active fee structures for a student's class + term."""
    return (FeeStructure.objects.filter(
        academic_year=term.academic_year,
        term=term,
        school_class=student.school_class,
        is_active=True,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))


def get_or_create_fee_account(student, term):
    """Get or create a fee account for a student + term, auto-calculating fees."""
    account, created = StudentFeeAccount.objects.get_or_create(
        student=student,
        academic_year=term.academic_year,
        term=term,
    )
    if created or account.total_fees <= 0:
        account.total_fees = total_fees_for(student, term)
        account.recalculate_status()
    return account


def create_fee_accounts_for_student(student, term=None):
    """Create fee accounts for a student (optionally for a specific term)."""
    if term is not None:
        terms = [term]
    else:
        term_ids = (FeeStructure.objects
                    .filter(school_class=student.school_class, is_active=True)
                    .values_list('term', flat=True).distinct())
        terms = list(Term.objects.filter(pk__in=term_ids))
    return [get_or_create_fee_account(student, t) for t in terms]


def refresh_account(account):
    """Recompute a fee account's total from structures and status from payments."""
    account.total_fees = total_fees_for(account.student, account.term)
    account.recalculate_status()
    return account


def active_fee_accounts():
    """Fee accounts in the current active academic year's first term, else all."""
    active_year = AcademicYear.objects.filter(is_active=True).first()
    active_term = None
    if active_year:
        active_term = (Term.objects.filter(academic_year=active_year)
                       .order_by('term_number').first())
    qs = StudentFeeAccount.objects.select_related('student', 'student__school_class')
    if active_term:
        qs = qs.filter(term=active_term)
    return qs
