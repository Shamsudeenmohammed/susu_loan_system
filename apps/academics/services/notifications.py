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


def _school_heads(school):
    from apps.accounts.models import User
    if school is None:
        return User.objects.none()
    return User.objects.filter(school=school, role='HEAD_TEACHER', is_active=True)


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
