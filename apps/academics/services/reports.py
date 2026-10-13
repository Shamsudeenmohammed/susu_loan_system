"""Terminal report assembly, including reconciled fee information.

Financial figures are **read from the existing fees module** (``StudentFeeAccount``
and ``FeePayment``) — this module never creates a second ledger and never
recomputes a payment. Fields that the underlying fees module does not model
(discounts, scholarships, waivers, refunds) are reported as *unavailable* and
flagged, rather than shown as a misleading zero.
"""

from datetime import date
from decimal import Decimal

from apps.core.utils import format_currency
from apps.school_fees.models import StudentFeeAccount

from ..models import AcademicSettings, TerminalComment, TerminalReportField
from . import attendance as attendance_service
from . import grading

DEFAULT_REPORT_FIELDS = [
    ('TOTAL_FEES', 10),
    ('ARREARS', 20),
    ('AMOUNT_PAID', 30),
    ('OUTSTANDING', 40),
    ('CREDIT', 50),
]

# Fields the existing fees ledger does not model. They are visible configurable
# options but always flagged as unavailable so nobody sees a fabricated amount.
UNMODELLED_FIELDS = {'DISCOUNT', 'SCHOLARSHIP', 'WAIVER', 'REFUND'}


def _year_key(year):
    return (year.start_date or date.min, year.name or '')


def _is_before(account, academic_year, term):
    """Whether ``account`` belongs to a period before the given year/term."""
    if account.academic_year_id == academic_year.pk:
        return account.term.term_number < term.term_number
    return _year_key(account.academic_year) < _year_key(academic_year)


def financial_summary(student, academic_year, term):
    """Reconcile a student's fees for one period against the fees module.

    Returns the current-period figures plus arrears from earlier periods and
    any advance credit. No value is invented: a period with no account is
    reported as ``has_account=False``.
    """
    accounts = list(
        StudentFeeAccount.objects.filter(student=student)
        .select_related('academic_year', 'term'))

    current = next(
        (a for a in accounts
         if a.academic_year_id == academic_year.pk and a.term_id == term.pk),
        None)

    arrears = Decimal('0.00')
    for account in accounts:
        if current is not None and account.pk == current.pk:
            continue
        if _is_before(account, academic_year, term):
            outstanding = account.total_fees - account.amount_paid
            if outstanding > 0:
                arrears += outstanding

    total_fees = current.total_fees if current else Decimal('0.00')
    amount_paid = current.amount_paid if current else Decimal('0.00')
    balance = total_fees - amount_paid
    outstanding = balance if balance > 0 else Decimal('0.00')
    credit = (-balance) if balance < 0 else Decimal('0.00')

    return {
        'has_account': current is not None,
        'account': current,
        'total_fees': total_fees,
        'amount_paid': amount_paid,
        'outstanding': outstanding,
        'arrears': arrears,
        'credit': credit,
        'account_balance': balance,
        # Not modelled by the fees ledger — deliberately not invented.
        'discount': None,
        'scholarship': None,
        'waiver': None,
        'refund': None,
    }


def _field_value(key, finance):
    """Map a report field key to ``(value, flag)``.

    ``flag`` is a human-readable reason when the figure cannot be trusted.
    """
    if key in UNMODELLED_FIELDS:
        return None, 'Not configured in the fees module'
    mapping = {
        'TOTAL_FEES': finance['total_fees'],
        'ARREARS': finance['arrears'],
        'AMOUNT_PAID': finance['amount_paid'],
        'OUTSTANDING': finance['outstanding'],
        'CREDIT': finance['credit'],
        'ACCOUNT_BALANCE': finance['account_balance'],
    }
    if key not in mapping:
        return None, 'Unknown field'
    if not finance['has_account'] and key in {'TOTAL_FEES', 'AMOUNT_PAID'}:
        return mapping[key], 'No fee account for this period'
    return mapping[key], ''


def report_fields(school, finance):
    """Visible, ordered report fields with formatted values and flags."""
    configs = list(
        TerminalReportField.objects.filter(school=school, is_visible=True)
        .order_by('display_order', 'pk'))
    if not configs:
        rows = []
        for key, order in DEFAULT_REPORT_FIELDS:
            value, flag = _field_value(key, finance)
            rows.append({
                'key': key,
                'label': dict(TerminalReportField.FieldKey.choices)[key],
                'value': value,
                'formatted': format_currency(value) if value is not None else '—',
                'flag': flag,
            })
        return rows

    rows = []
    for cfg in configs:
        value, flag = _field_value(cfg.key, finance)
        rows.append({
            'key': cfg.key,
            'label': cfg.resolved_label,
            'value': value,
            'formatted': format_currency(value) if value is not None else '—',
            'flag': flag,
        })
    return rows


def terminal_report_data(student, academic_year, term, *, subjects=None,
                         include_finance=True):
    """Assemble everything a terminal report needs for one student."""
    settings_obj = AcademicSettings.for_school(student.school)
    scale = grading.resolve_scale(student.school)
    results = grading.student_term_report(
        student, academic_year, term, scale=scale, subjects=subjects)
    attendance = attendance_service.student_summary(student, academic_year, term)
    comment = TerminalComment.objects.filter(
        student=student, academic_year=academic_year, term=term).first()

    finance = None
    fields = []
    if include_finance:
        finance = financial_summary(student, academic_year, term)
        fields = report_fields(student.school, finance)

    return {
        'student': student,
        'school': student.school,
        'academic_year': academic_year,
        'term': term,
        'settings': settings_obj,
        'scale': scale,
        'results': results,
        'attendance': attendance,
        'comment': comment,
        'finance': finance,
        'report_fields': fields,
        'currency': (settings_obj.currency if settings_obj else 'GHS'),
    }
