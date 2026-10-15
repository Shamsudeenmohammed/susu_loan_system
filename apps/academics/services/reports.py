"""Terminal report assembly, including reconciled fee information.

Financial figures are **read from the existing fees module** (``StudentFeeAccount``
and ``FeePayment``) — this module never creates a second ledger and never
recomputes a payment. Fields that the underlying fees module does not model
(discounts, scholarships, waivers, refunds) are reported as *unavailable* and
flagged, rather than shown as a misleading zero.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.utils import format_currency
from apps.school_fees.models import StudentFeeAccount
from apps.schools.scoping import can_access_school

from ..models import (
    AcademicSettings,
    TerminalComment,
    TerminalReport,
    TerminalReportField,
)
from . import attendance as attendance_service
from . import grading
from . import notifications

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


# ---------------------------------------------------------------------------
# Official terminal report workflow: generate -> approve -> publish
# ---------------------------------------------------------------------------
#: Roles allowed to approve/publish official reports.
REVIEW_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')


class ReportError(Exception):
    """Raised when a report lifecycle operation is not allowed."""


def can_generate(user):
    from apps.academics.services import access
    return bool(getattr(user, 'is_authenticated', False)
                and (user.is_superuser or access.can_configure(user)
                     or access.can_review(user)))


def can_approve(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_superuser or user.has_role(*REVIEW_ROLES))


can_publish = can_approve


def _class_position(student, academic_year, term, show_positions):
    """Return the student's position in their class, or ``None``.

    Positions are only produced when the school has enabled them, matching the
    results page. A missing setting never invents a rank.
    """
    if not show_positions:
        return None
    from . import results as results_service

    rows, _subjects = results_service.class_results(
        student.school_class, academic_year, term, students=None)
    for row in rows:
        if row['student'].pk == student.pk:
            return row.get('position')
    return None


def generation_blockers(student, academic_year, term, data):
    """Human-readable reasons a report cannot be finalised.

    The rules are read from the school's settings: results must be complete if
    the school requires it, and attendance must exist if configured.
    """
    blockers = []
    settings_obj = data.get('settings')
    results = data.get('results') or {}
    attendance = data.get('attendance') or {}
    if settings_obj is not None and settings_obj.require_complete_results_for_publication:
        incomplete = [
            r['subject'].name for r in results.get('subjects', [])
            if r.get('incomplete')
        ]
        if incomplete:
            blockers.append(
                'Results are incomplete for: ' + ', '.join(incomplete) + '.')
    if settings_obj is not None and settings_obj.require_attendance_for_publication:
        if not attendance.get('total'):
            blockers.append('No attendance has been recorded for this period.')
    return blockers


def build_snapshot(student, academic_year, term, data):
    """A frozen, JSON-serialisable copy of the figures at generation time."""
    results = data.get('results') or {}
    attendance = data.get('attendance') or {}
    comment = data.get('comment')
    finance = data.get('finance') or {}
    return {
        'generated_at': timezone.now().isoformat(),
        'student': {
            'id': student.pk,
            'student_id': student.student_id,
            'name': student.get_full_name(),
            'school_class': student.school_class.name if student.school_class_id else '',
        },
        'academic_year': academic_year.name,
        'term': term.name,
        'subjects': [
            {
                'subject': r['subject'].name,
                'percentage': str(r['percentage']) if r['percentage'] is not None else None,
                'grade': r['grade'] or '',
                'remark': r['remark'] or '',
                'incomplete': bool(r.get('incomplete')),
            }
            for r in results.get('subjects', [])
        ],
        'average': str(results['average']) if results.get('average') is not None else None,
        'total': str(results['total']) if results.get('total') is not None else None,
        'complete': bool(results.get('complete')),
        'attendance': {
            'present': attendance.get('present', 0),
            'absent': attendance.get('absent', 0),
            'late': attendance.get('late', 0),
            'excused': attendance.get('excused', 0),
            'total': attendance.get('total', 0),
            'percentage': (str(attendance['percentage'])
                           if attendance.get('percentage') is not None else None),
        },
        'finance': {
            'has_account': finance.get('has_account', False),
            'total_fees': str(finance.get('total_fees', Decimal('0.00'))),
            'amount_paid': str(finance.get('amount_paid', Decimal('0.00'))),
            'outstanding': str(finance.get('outstanding', Decimal('0.00'))),
            'arrears': str(finance.get('arrears', Decimal('0.00'))),
            'credit': str(finance.get('credit', Decimal('0.00'))),
        },
        'class_teacher_comment': comment.class_teacher_comment if comment else '',
        'head_teacher_remark': comment.head_teacher_remark if comment else '',
        'promotion_status': (comment.promotion_status if comment
                             else TerminalComment.PromotionStatus.PENDING),
    }


def latest_report(student, academic_year, term):
    """The most recent report version for a student/period (or ``None``)."""
    return (TerminalReport.objects
            .filter(student=student, academic_year=academic_year, term=term)
            .exclude(status=TerminalReport.Status.SUPERSEDED)
            .order_by('-version', '-created_at')
            .first())


def official_report(student, academic_year, term):
    """The published, not-superseded report for a student/period (or ``None``)."""
    return (TerminalReport.objects
            .filter(student=student, academic_year=academic_year, term=term,
                    status=TerminalReport.Status.PUBLISHED,
                    superseded_by__isnull=True)
            .order_by('-version', '-created_at')
            .first())


def report_history(student, academic_year, term):
    return (TerminalReport.objects
            .filter(student=student, academic_year=academic_year, term=term)
            .select_related('generated_by', 'approved_by', 'published_by',
                            'amended_by', 'superseded_by', 'school_class')
            .order_by('-version', '-created_at'))


def _audit(action, description, user, report):
    AuditLog.log(
        action, description, user=user,
        object_type='academics.TerminalReport', object_id=report.pk)


@transaction.atomic
def generate_terminal_report(student, academic_year, term, user, *,
                             override_reason='', force=False):
    """Generate a new report version snapshotting the current figures.

    Incomplete data warns/blocks according to the school's settings. A reason
    is required (and audited) to override a blocker. Existing versions are kept
    intact; a new version number is allocated.
    """
    if not can_access_school(user, student.school):
        raise ReportError('You cannot generate reports for that school.')
    if not can_generate(user):
        raise ReportError('You are not allowed to generate terminal reports.')

    data = terminal_report_data(student, academic_year, term)
    blockers = generation_blockers(student, academic_year, term, data)
    override_reason = (override_reason or '').strip()
    if blockers and not force:
        raise ReportError(' '.join(blockers))
    if blockers and force and not override_reason:
        raise ReportError(
            'A reason is required to generate a report with incomplete data.')

    settings_obj = data.get('settings')
    show_positions = bool(settings_obj and settings_obj.show_position_on_report)
    position = _class_position(student, academic_year, term, show_positions)
    comment = data.get('comment')
    finance = data.get('finance')
    results = data.get('results') or {}
    attendance = data.get('attendance') or {}

    last_version = (TerminalReport.objects
                    .filter(student=student, academic_year=academic_year,
                            term=term)
                    .order_by('-version')
                    .values_list('version', flat=True)
                    .first()) or 0

    report = TerminalReport.objects.create(
        school=student.school,
        student=student,
        school_class=student.school_class,
        academic_year=academic_year,
        term=term,
        status=TerminalReport.Status.GENERATED,
        version=last_version + 1,
        snapshot=build_snapshot(student, academic_year, term, data),
        average=results.get('average'),
        total=results.get('total'),
        attendance_percentage=attendance.get('percentage'),
        position=position,
        total_fees=finance.get('total_fees', Decimal('0.00')) if finance else Decimal('0.00'),
        amount_paid=finance.get('amount_paid', Decimal('0.00')) if finance else Decimal('0.00'),
        outstanding=finance.get('outstanding', Decimal('0.00')) if finance else Decimal('0.00'),
        arrears=finance.get('arrears', Decimal('0.00')) if finance else Decimal('0.00'),
        credit=finance.get('credit', Decimal('0.00')) if finance else Decimal('0.00'),
        promotion_status=(comment.promotion_status if comment
                          else TerminalComment.PromotionStatus.PENDING),
        class_teacher_comment=comment.class_teacher_comment if comment else '',
        head_teacher_remark=comment.head_teacher_remark if comment else '',
        generated_by=user,
        generated_at=timezone.now(),
        amendment_reason=override_reason,
    )
    note = f'Report v{report.version} generated.'
    if blockers:
        note += ' Override reason: ' + override_reason
    _audit(AuditLog.ActionType.REPORT_GENERATED, note, user, report)
    return report


@transaction.atomic
def approve_terminal_report(report, user, comment=''):
    """Approve a generated report so it can be published."""
    if not can_approve(user):
        raise ReportError('You are not allowed to approve terminal reports.')
    if not can_access_school(user, report.school):
        raise ReportError('You cannot approve reports for that school.')
    if report.status == TerminalReport.Status.GENERATED:
        pass
    elif report.status in (TerminalReport.Status.APPROVED,
                           TerminalReport.Status.PUBLISHED):
        raise ReportError('That report is already approved.')
    else:
        raise ReportError(
            f'A report that is {report.get_status_display().lower()} cannot be '
            f'approved.')

    report.status = TerminalReport.Status.APPROVED
    report.approved_by = user
    report.approved_at = timezone.now()
    report.approval_comment = (comment or '').strip()
    report.save(update_fields=[
        'status', 'approved_by', 'approved_at', 'approval_comment', 'updated_at'])
    _audit(
        AuditLog.ActionType.REPORT_APPROVED,
        f'Report v{report.version} for {report.student.get_full_name()} '
        f'approved.', user, report)
    return report


@transaction.atomic
def publish_terminal_report(report, user, *, override_reason='', force=False):
    """Publish an approved report as the student's official report.

    Any previously published version for the same period is superseded (kept,
    linked through ``superseded_by``) so history is never lost. Parents are
    notified only once the report is official.
    """
    if not can_publish(user):
        raise ReportError('You are not allowed to publish terminal reports.')
    if not can_access_school(user, report.school):
        raise ReportError('You cannot publish reports for that school.')
    if report.status != TerminalReport.Status.APPROVED:
        raise ReportError('Only an approved report can be published.')

    override_reason = (override_reason or '').strip()
    blockers = generation_blockers(
        report.student, report.academic_year, report.term,
        terminal_report_data(report.student, report.academic_year, report.term))
    if blockers and not force:
        raise ReportError(' '.join(blockers))
    if blockers and force and not override_reason:
        raise ReportError(
            'A reason is required to publish a report with incomplete data.')

    previous = (TerminalReport.objects
                .filter(student=report.student,
                        academic_year=report.academic_year,
                        term=report.term,
                        status=TerminalReport.Status.PUBLISHED)
                .exclude(pk=report.pk))
    for old in previous:
        old.status = TerminalReport.Status.SUPERSEDED
        old.superseded_by = report
        old.save(update_fields=['status', 'superseded_by', 'updated_at'])
        _audit(
            AuditLog.ActionType.REPORT_SUPERSEDED,
            f'Report v{old.version} superseded by v{report.version}.',
            user, old)

    report.status = TerminalReport.Status.PUBLISHED
    report.published_by = user
    report.published_at = timezone.now()
    if override_reason:
        report.amendment_reason = override_reason
    report.save(update_fields=[
        'status', 'published_by', 'published_at', 'amendment_reason', 'updated_at'])
    _audit(
        AuditLog.ActionType.REPORT_PUBLISHED,
        f'Report v{report.version} for {report.student.get_full_name()} '
        f'published as the official report.', user, report)
    notifications.notify_terminal_report_published(report)
    return report


@transaction.atomic
def return_terminal_report(report, user, comment=''):
    """Send a generated report back to the generator for correction."""
    if not can_approve(user):
        raise ReportError('You are not allowed to review terminal reports.')
    if not can_access_school(user, report.school):
        raise ReportError('You cannot review reports for that school.')
    if report.status != TerminalReport.Status.GENERATED:
        raise ReportError('Only a generated report can be returned.')
    comment = (comment or '').strip()
    if not comment:
        raise ReportError('Explain what must be corrected.')
    report.status = TerminalReport.Status.DRAFT
    report.approval_comment = comment
    report.save(update_fields=['status', 'approval_comment', 'updated_at'])
    _audit(
        AuditLog.ActionType.REPORT_AMENDED,
        f'Report v{report.version} returned for correction: {comment[:120]}',
        user, report)
    notifications.notify_report_correction_requested(report, user)
    return report


@transaction.atomic
def amend_terminal_report(report, user, reason=''):
    """Supersede an official report and generate a corrected replacement.

    Used after an approved correction to the underlying marks/attendance: the
    published report is marked ``SUPERSEDED`` (kept for the audit trail) and a
    fresh version is generated from the current data. The replacement starts as
    ``GENERATED`` so it must be approved and published again.
    """
    if not can_approve(user):
        raise ReportError('You are not allowed to amend terminal reports.')
    if not can_access_school(user, report.school):
        raise ReportError('You cannot amend reports for that school.')
    reason = (reason or '').strip()
    if not reason:
        raise ReportError('A reason is required to amend an official report.')
    if report.status not in (TerminalReport.Status.PUBLISHED,
                             TerminalReport.Status.APPROVED,
                             TerminalReport.Status.GENERATED):
        raise ReportError(
            f'A report that is {report.get_status_display().lower()} cannot be '
            f'amended.')

    replacement = generate_terminal_report(
        report.student, report.academic_year, report.term, user, force=True,
        override_reason=reason)
    report.status = TerminalReport.Status.SUPERSEDED
    report.superseded_by = replacement
    report.amended_by = user
    report.amended_at = timezone.now()
    report.amendment_reason = reason
    report.save(update_fields=[
        'status', 'superseded_by', 'amended_by', 'amended_at',
        'amendment_reason', 'updated_at'])
    _audit(
        AuditLog.ActionType.REPORT_AMENDED,
        f'Report v{report.version} superseded for amendment: {reason[:120]}',
        user, report)
    return replacement
