import logging
from decimal import Decimal

from django.db.models import Sum

from ..models import (
    FeeStructure,
    StudentFeeAccount,
    AcademicYear,
    Term,
    SchoolClass,
    FeeCategory,
    Student,
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


def _upsert_fee_structure(term, school_class, fee_category, amount, due_date, description=''):
    """Create or update a fee structure for a term + class + category."""
    structure, created = FeeStructure.objects.get_or_create(
        academic_year=term.academic_year,
        term=term,
        school_class=school_class,
        fee_category=fee_category,
        defaults={
            'amount': amount,
            'due_date': due_date,
            'description': description,
            'is_active': True,
        },
    )
    if not created:
        changed = False
        if structure.amount != amount:
            structure.amount = amount
            changed = True
        if structure.due_date != due_date:
            structure.due_date = due_date
            changed = True
        if structure.description != description and description:
            structure.description = description
            changed = True
        if not structure.is_active:
            structure.is_active = True
            changed = True
        if changed:
            structure.save()
    return structure


def assign_fee_results(term_classes, fee_category, amount, due_date, description=''):
    """Return (assignments, structures) for a set of (term, class) combinations."""
    structures = []
    for term, school_class in term_classes:
        structures.append(_upsert_fee_structure(
            term, school_class, fee_category, amount, due_date, description))
    return structures


def assign_fee_to_classes(term, classes, fee_category, amount, due_date, description=''):
    """Assign a fee to all active students in the given classes."""
    structures = []
    accounts = []
    for school_class in classes:
        structure = _upsert_fee_structure(
            term, school_class, fee_category, amount, due_date, description)
        structures.append(structure)
        if structure.is_active:
            students = Student.objects.filter(school_class=school_class, is_active=True)
            for student in students:
                accounts.append(get_or_create_fee_account(student, term))
    return {'structures': structures, 'accounts': accounts}


def assign_fee_to_all(term, fee_category, amount, due_date, description=''):
    """Assign a fee to all active students across every class."""
    classes = SchoolClass.objects.filter(is_active=True, students__is_active=True).distinct()
    return assign_fee_to_classes(term, classes, fee_category, amount, due_date, description)


def assign_fee_to_student(student, term, fee_category, amount, due_date, description=''):
    """Assign a fee to a single student."""
    structure = _upsert_fee_structure(
        term, student.school_class, fee_category, amount, due_date, description)
    account = get_or_create_fee_account(student, term)
    return {
        'structures': [structure],
        'accounts': [account],
        'student': student,
    }
