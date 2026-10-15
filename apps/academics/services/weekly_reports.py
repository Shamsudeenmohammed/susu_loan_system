"""Weekly teacher reports.

A teacher drafts a short weekly narrative for one of their classes, submits it
to the Head Teacher and the submission history is preserved. The
``(teacher, class, academic year, week_start)`` combination is unique, so the
same week can never be reported twice: an authorised reviewer who needs changes
requests clarification, which reopens the *same* row for revision.

Role rules
----------

* A **teacher** may create/edit their own draft, submit it and read their own
  reports. They cannot review.
* A **head-level** user (head teacher, admin, proprietor) may review any report
  in a school they can access: acknowledge, request clarification or accept it.

All queries are expected to be school-scoped by the caller; the helpers here
add the object-level ownership checks.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.schools.scoping import can_access_school

from ..models import WeeklyReport, WeeklyReportEvent
from . import notifications

#: Roles that review weekly reports.
REVIEW_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')

#: Allowed ``(from_status, to_status)`` pairs for the owning teacher.
TEACHER_TRANSITIONS = {
    (WeeklyReport.Status.DRAFT, WeeklyReport.Status.SUBMITTED),
    (WeeklyReport.Status.CLARIFICATION_REQUESTED, WeeklyReport.Status.SUBMITTED),
}

#: Allowed ``(from_status, to_status)`` pairs for a reviewer.
REVIEW_TRANSITIONS = {
    (WeeklyReport.Status.SUBMITTED, WeeklyReport.Status.ACKNOWLEDGED),
    (WeeklyReport.Status.SUBMITTED, WeeklyReport.Status.CLARIFICATION_REQUESTED),
    (WeeklyReport.Status.SUBMITTED, WeeklyReport.Status.REVIEWED),
    (WeeklyReport.Status.ACKNOWLEDGED, WeeklyReport.Status.REVIEWED),
    (WeeklyReport.Status.ACKNOWLEDGED, WeeklyReport.Status.CLARIFICATION_REQUESTED),
}


class WeeklyReportError(Exception):
    """Raised when a weekly-report operation is not allowed."""


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------
def week_start_for(value):
    """Return the Monday of the week containing ``value``."""
    return value - timedelta(days=value.weekday())


def current_week_start(today=None):
    """The Monday of the current week (server-local date)."""
    today = today or timezone.localdate()
    return week_start_for(today)


def week_bounds(week_start):
    """Return ``(week_start, week_end)`` with Sunday as the last day."""
    return week_start, week_start + timedelta(days=6)


def week_dates(week_start, days=5):
    """The dates in a reporting week (default Monday–Friday)."""
    return [week_start + timedelta(days=offset) for offset in range(days)]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def can_view(user, report):
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, report.school):
        return False
    if user.is_superuser:
        return True
    if report.teacher_id == user.pk:
        return True
    return user.has_role(*REVIEW_ROLES)


def can_edit(user, report):
    """Whether ``user`` may change the report's content."""
    if not can_view(user, report):
        return False
    if not report.is_editable:
        return False
    return bool(user.is_superuser or report.teacher_id == user.pk)


def can_review(user, report):
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, report.school):
        return False
    return bool(user.is_superuser or user.has_role(*REVIEW_ROLES))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _log_event(report, user, action, notes=''):
    WeeklyReportEvent.objects.create(
        report=report, user=user, action=action, notes=notes or '')


def _audit(action, description, user, report):
    AuditLog.log(
        action, description, user=user,
        object_type='academics.WeeklyReport', object_id=report.pk)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
@transaction.atomic
def save_draft(*, actor, school, school_class, academic_year, term, week_start,
               summary, subject=None, challenges='', support_needed='',
               next_week_plan='', attendance_notes='', teacher=None, report=None):
    """Create or update a draft weekly report.

    ``teacher`` is the report owner (defaults to ``actor``); ``actor`` is
    whoever is making the change, so a reviewer filing on a teacher's behalf is
    recorded correctly. If a row already exists for the teacher/class/year/week
    and is still editable (draft or clarification requested) it is updated in
    place; if it has already been submitted/reviewed a
    :class:`WeeklyReportError` is raised so the same week is never duplicated.
    """
    teacher = teacher or actor
    if school is None:
        raise WeeklyReportError('A school is required.')
    if not can_access_school(actor, school):
        raise WeeklyReportError('You cannot file reports for that school.')
    if school_class is None:
        raise WeeklyReportError('Choose the class this report is for.')
    if school_class.school_id != school.pk:
        raise WeeklyReportError('The class must belong to the same school.')
    if academic_year is None:
        raise WeeklyReportError('Choose an academic year.')
    if term is None:
        raise WeeklyReportError('Choose a term.')
    summary = (summary or '').strip()
    if not summary:
        raise WeeklyReportError('A summary of what was covered is required.')

    week_start = week_start_for(week_start)
    week_end = week_start + timedelta(days=6)

    existing = WeeklyReport.objects.filter(
        teacher=teacher, school_class=school_class, academic_year=academic_year,
        week_start=week_start).first()
    if report is None:
        report = existing
    if report is not None and existing is not None and report.pk != existing.pk:
        raise WeeklyReportError(
            'A report for that class and week already exists.')

    if report is not None:
        if report.teacher_id not in (None, teacher.pk):
            raise WeeklyReportError('That report belongs to another teacher.')
        if not report.is_editable:
            raise WeeklyReportError(
                'This report has already been submitted and can no longer be '
                'edited. Ask the Head Teacher to request clarification.')
        created = False
    else:
        created = True

    if report is None:
        report = WeeklyReport(
            school=school, teacher=teacher, school_class=school_class,
            academic_year=academic_year, term=term)

    report.school = school
    report.teacher = teacher
    report.school_class = school_class
    report.academic_year = academic_year
    report.term = term
    report.subject = subject
    report.week_start = week_start
    report.week_end = week_end
    report.summary = summary
    report.challenges = (challenges or '').strip()
    report.support_needed = (support_needed or '').strip()
    report.next_week_plan = (next_week_plan or '').strip()
    report.attendance_notes = (attendance_notes or '').strip()
    report.save()

    _log_event(
        report, actor,
        WeeklyReportEvent.Action.CREATED if created else WeeklyReportEvent.Action.UPDATED,
        'Draft created.' if created else 'Draft updated.')
    return report


@transaction.atomic
def submit(report, user, notes=''):
    """Submit a draft (or a clarified draft) to the Head Teacher."""
    if not can_access_school(user, report.school):
        raise WeeklyReportError('You cannot submit that report.')
    if not (user.is_superuser or report.teacher_id == user.pk):
        raise WeeklyReportError('Only the teacher who owns this report may submit it.')
    if (report.status, WeeklyReport.Status.SUBMITTED) not in TEACHER_TRANSITIONS:
        raise WeeklyReportError(
            f'A report that is {report.get_status_display().lower()} cannot be '
            f'submitted.')

    report.status = WeeklyReport.Status.SUBMITTED
    report.submitted_by = user
    report.submitted_at = timezone.now()
    report.save(update_fields=[
        'status', 'submitted_by', 'submitted_at', 'updated_at'])
    _log_event(report, user, WeeklyReportEvent.Action.SUBMITTED, notes)
    _audit(
        AuditLog.ActionType.WEEKLY_REPORT_SUBMITTED,
        f'Weekly report for {report.school_class} ({report.period_label()}) '
        f'submitted for review.', user, report)
    notifications.notify_weekly_report_submitted(report)
    return report


@transaction.atomic
def review(report, user, to_status, comment=''):
    """Reviewer action: acknowledge, request clarification or accept."""
    to_status = (to_status or '').strip().upper()
    if to_status not in dict(WeeklyReport.Status.choices):
        raise WeeklyReportError('Unknown weekly-report status.')
    if not can_review(user, report):
        raise WeeklyReportError('You are not allowed to review that report.')
    if (report.status, to_status) not in REVIEW_TRANSITIONS:
        raise WeeklyReportError(
            f'Cannot move a report from {report.get_status_display()} '
            f'to {dict(WeeklyReport.Status.choices)[to_status]}.')

    comment = (comment or '').strip()
    if to_status == WeeklyReport.Status.CLARIFICATION_REQUESTED and not comment:
        raise WeeklyReportError(
            'Please explain what clarification or correction is needed.')

    report.status = to_status
    report.reviewed_by = user
    report.reviewed_at = timezone.now()
    report.review_comment = comment
    report.save(update_fields=[
        'status', 'reviewed_by', 'reviewed_at', 'review_comment', 'updated_at'])

    action = {
        WeeklyReport.Status.ACKNOWLEDGED: WeeklyReportEvent.Action.ACKNOWLEDGED,
        WeeklyReport.Status.CLARIFICATION_REQUESTED:
            WeeklyReportEvent.Action.CLARIFICATION_REQUESTED,
        WeeklyReport.Status.REVIEWED: WeeklyReportEvent.Action.REVIEWED,
    }.get(to_status, WeeklyReportEvent.Action.COMMENTED)
    _log_event(report, user, action, comment)
    _audit(
        AuditLog.ActionType.WEEKLY_REPORT_REVIEWED,
        f'Weekly report for {report.school_class} ({report.period_label()}) '
        f'moved to {report.get_status_display()}.', user, report)
    notifications.notify_weekly_report_reviewed(report)
    return report


@transaction.atomic
def add_comment(report, user, text):
    """Append a comment to a report's history (both parties may comment)."""
    text = (text or '').strip()
    if not text:
        raise WeeklyReportError('Comment cannot be empty.')
    if not can_view(user, report):
        raise WeeklyReportError('You are not involved in this report.')
    _log_event(report, user, WeeklyReportEvent.Action.COMMENTED, text)
    return report
