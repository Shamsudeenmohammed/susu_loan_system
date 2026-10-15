"""Academic notifications, routed through the shared SMS gateway.

Sending is best-effort: a failure to notify must never break a mark submission
or a publication. ``unique_key`` values give the underlying SMS service
idempotency, so retrying an operation does not send a duplicate message.
"""

import logging

from apps.notifications.providers.exceptions import SMSProviderError
from apps.notifications.services.sms import SMSValidationError, get_sms_service

logger = logging.getLogger('apps.academics')

NOTIFICATION_TYPE = 'ACADEMIC'


def notify_user(user, message, unique_key, notification_type=NOTIFICATION_TYPE):
    """Best-effort SMS to a staff user. Returns the notification or ``None``."""
    if user is None or not getattr(user, 'phone', ''):
        return None
    try:
        return get_sms_service().send_sms(
            phone_number=user.phone,
            message=message,
            notification_type=notification_type,
            unique_key=unique_key,
            reference_model='academics',
        )
    except SMSValidationError:
        logger.warning('Invalid phone for academic notification: %s', user.pk)
        return None
    except SMSProviderError:
        logger.exception('SMS provider error for academic notification')
        return None


def notify_phone(phone, message, unique_key, notification_type=NOTIFICATION_TYPE):
    """Best-effort SMS to a raw phone number (e.g. a parent).

    No sensitive academic detail is ever included by the callers: the message
    only says a report is available. ``unique_key`` keeps the send idempotent.
    """
    if not phone:
        return None
    try:
        return get_sms_service().send_sms(
            phone_number=phone,
            message=message,
            notification_type=notification_type,
            unique_key=unique_key,
            reference_model='academics',
        )
    except SMSValidationError:
        logger.warning('Invalid phone for parent academic notification')
        return None
    except SMSProviderError:
        logger.exception('SMS provider error for parent academic notification')
        return None


def _school_heads(school):
    from apps.accounts.models import User
    if school is None:
        return User.objects.none()
    return User.objects.filter(
        school=school, role__in=['HEAD_TEACHER', 'ADMIN'], is_active=True)


def notify_marks_submitted(submission):
    """Tell the head teacher that marks await review."""
    message = (
        f"{submission.school_class} {submission.subject}: marks submitted "
        f"for {submission.term} and are awaiting your review.")
    for head in _school_heads(submission.school):
        notify_user(head, message, f'acad:submitted:{submission.pk}:{head.pk}')


def notify_submission_reviewed(submission):
    """Tell the teacher their submission was reviewed (approved/returned)."""
    if submission.submitted_by is None:
        return
    state = submission.get_status_display()
    message = (
        f"Your marks for {submission.school_class} {submission.subject} "
        f"({submission.term}) were {state}.")
    if submission.review_comment:
        message += f" Comment: {submission.review_comment[:80]}"
    notify_user(
        submission.submitted_by, message,
        f'acad:reviewed:{submission.pk}:{submission.status}')


def notify_results_published(publication):
    """Tell a class's teachers that results were published."""
    from ..models import TeacherAssignment

    message = (
        f"Results for {publication.school_class} ({publication.term}) have been "
        f"published. Terminal reports are now available.")
    teacher_ids = (
        TeacherAssignment.objects
        .filter(school_class=publication.school_class, is_active=True)
        .values_list('teacher_id', flat=True)
        .distinct())
    from apps.accounts.models import User
    for teacher in User.objects.filter(pk__in=list(teacher_ids), is_active=True):
        notify_user(
            teacher, message,
            f'acad:published:{publication.pk}:{teacher.pk}')


def notify_request_created(request_obj):
    """Tell the assigned teacher a request has been created for them."""
    if request_obj.assigned_to is None:
        return
    message = (
        f"New {request_obj.get_priority_display()} request from the Head "
        f"Teacher: {request_obj.title}. "
        f"Due {request_obj.due_date or 'as soon as possible'}.")
    if request_obj.school_class_id:
        message += f" ({request_obj.school_class.name})"
    notify_user(
        request_obj.assigned_to, message,
        f'acad:req-created:{request_obj.pk}')


def notify_request_transitioned(request_obj, actor):
    """Tell the counterpart of a request that its status changed.

    The head teacher(s) are told when the teacher submits work back; the
    teacher is told when the request is confirmed, reopened or cancelled.
    """
    if request_obj.status == 'SUBMITTED':
        message = (
            f"The assigned teacher submitted work for request "
            f"#{request_obj.pk} - {request_obj.title}. Confirm or reopen it.")
        for head in _school_heads(request_obj.school):
            notify_user(head, message, f'acad:req-submitted:{request_obj.pk}:{head.pk}')
        return
    if request_obj.assigned_to is None:
        return
    if actor is not None and actor.pk == request_obj.assigned_to_id:
        return
    message = (
        f"Request #{request_obj.pk} - {request_obj.title} is now "
        f"{request_obj.get_status_display().lower()}.")
    notify_user(
        request_obj.assigned_to, message,
        f'acad:req-status:{request_obj.pk}:{request_obj.status}')


# ---------------------------------------------------------------------------
# Weekly teacher reports
# ---------------------------------------------------------------------------
def notify_weekly_report_submitted(report):
    """Tell the school's head teachers a weekly report awaits review."""
    message = (
        f"{report.teacher.get_full_name() or report.teacher.username} submitted "
        f"the weekly report for {report.school_class} "
        f"({report.period_label()}) and it is awaiting your review.")
    for head in _school_heads(report.school):
        notify_user(
            head, message,
            f'acad:weekly-submitted:{report.pk}:{head.pk}')


def notify_weekly_report_reviewed(report):
    """Tell the teacher the outcome of their weekly report review."""
    if report.teacher_id is None:
        return
    state = report.get_status_display().lower()
    message = (
        f"Your weekly report for {report.school_class} "
        f"({report.period_label()}) is now {state}.")
    if report.review_comment:
        message += f" Comment: {report.review_comment[:80]}"
    notify_user(
        report.teacher, message,
        f'acad:weekly-review:{report.pk}:{report.status}')


# ---------------------------------------------------------------------------
# Complaints, requests and safeguarding concerns
# ---------------------------------------------------------------------------
def notify_complaint_created(complaint):
    """Tell head teachers about a new concern; escalate urgent ones at once.

    The message deliberately carries no sensitive description: for a
    safeguarding or urgent case the head teacher is asked to open the case in
    the portal rather than being sent the confidential detail by SMS.
    """
    if complaint.is_escalated:
        prefix = 'URGENT safeguarding/priority concern'
    else:
        prefix = complaint.get_case_type_display()
    message = (
        f"{prefix} raised by "
        f"{complaint.raised_by.get_full_name() if complaint.raised_by else 'a staff member'}"
        f" ({complaint.get_priority_display()} priority). "
        f"Open the portal to review case #{complaint.pk}.")
    for head in _school_heads(complaint.school):
        notify_user(
            head, message,
            f'acad:complaint-created:{complaint.pk}:{head.pk}')


def notify_complaint_transitioned(complaint, actor):
    """Tell the person who raised a case that its status changed."""
    if complaint.raised_by is None:
        return
    if actor is not None and getattr(actor, 'pk', None) == complaint.raised_by_id:
        # Do not notify the actor of their own action.
        return
    message = (
        f"Your {complaint.get_case_type_display().lower()} "
        f"\"{complaint.title[:60]}\" is now "
        f"{complaint.get_status_display().lower()}.")
    if complaint.resolution_notes:
        message += f" Notes: {complaint.resolution_notes[:80]}"
    notify_user(
        complaint.raised_by, message,
        f'acad:complaint-status:{complaint.pk}:{complaint.status}')


# ---------------------------------------------------------------------------
# Official terminal reports
# ---------------------------------------------------------------------------
def notify_terminal_report_published(report):
    """Tell a parent that their child's official report is available.

    Only sent after the report has been officially published. The message
    never contains marks, grades or fee figures.
    """
    student = report.student
    phone = getattr(student, 'parent_phone', '')
    if not phone:
        return None
    # The student's name is not confidential to the parent; marks are.
    message = (
        f"The {report.term.name} terminal report for {student.get_full_name()} "
        f"is now available at {report.school.display_name}. "
        f"Please contact the school office to collect it.")
    return notify_phone(
        phone, message,
        f'acad:report-published:{report.pk}')


def notify_report_correction_requested(report, user):
    """Tell the generating teacher a report was returned for correction."""
    if report.generated_by is None:
        return
    if user is not None and getattr(user, 'pk', None) == report.generated_by_id:
        return
    message = (
        f"The terminal report for {report.student.get_full_name()} "
        f"({report.term.name}) needs correction before it can be approved.")
    if report.approval_comment:
        message += f" Reason: {report.approval_comment[:80]}"
    notify_user(
        report.generated_by, message,
        f'acad:report-correction:{report.pk}')
