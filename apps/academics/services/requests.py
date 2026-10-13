"""Update Request Centre - Head Teacher requests & follow-ups.

The centre lives in the academics module and reuses the existing school
scoping, notifications (SMS via :mod:`.notifications`) and audit log. Requests
never cross school boundaries: every request is a ``SchoolOwnedModel`` and the
views query them through :mod:`apps.schools.scoping`.

Role rules
----------

* A **head-level** user (head teacher, admin, proprietor) may create requests,
  review responses, confirm completion, cancel and reopen requests within the
  schools they can access.
* A **teacher** may only act on requests assigned to them. They can start a
  pending request, respond, and submit it back for confirmation.
* Status transitions are validated server-side (see :data:`TRANSITIONS`).
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.schools.scoping import can_access_school

from ..models import UpdateRequest, UpdateRequestEvent
from . import notifications

User = get_user_model()


class RequestError(Exception):
    """Raised when an update-request operation is not allowed."""


#: Statuses whose due dates are still being tracked.
OPEN_STATUSES = (UpdateRequest.Status.PENDING, UpdateRequest.Status.IN_PROGRESS)

#: Roles that manage requests (create / confirm / cancel / reopen).
MANAGER_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')

#: Allowed ``(from_status, to_status)`` pairs for the assigned teacher.
TEACHER_TRANSITIONS = {
    (UpdateRequest.Status.PENDING, UpdateRequest.Status.IN_PROGRESS),
    (UpdateRequest.Status.PENDING, UpdateRequest.Status.SUBMITTED),
    (UpdateRequest.Status.IN_PROGRESS, UpdateRequest.Status.SUBMITTED),
}

#: Allowed ``(from_status, to_status)`` pairs for a head-level manager.
MANAGER_TRANSITIONS = {
    (UpdateRequest.Status.PENDING, UpdateRequest.Status.IN_PROGRESS),
    (UpdateRequest.Status.SUBMITTED, UpdateRequest.Status.COMPLETED),
    (UpdateRequest.Status.SUBMITTED, UpdateRequest.Status.IN_PROGRESS),
    (UpdateRequest.Status.IN_PROGRESS, UpdateRequest.Status.PENDING),
    (UpdateRequest.Status.COMPLETED, UpdateRequest.Status.PENDING),
    (UpdateRequest.Status.CANCELLED, UpdateRequest.Status.PENDING),
    (UpdateRequest.Status.PENDING, UpdateRequest.Status.CANCELLED),
    (UpdateRequest.Status.IN_PROGRESS, UpdateRequest.Status.CANCELLED),
    (UpdateRequest.Status.SUBMITTED, UpdateRequest.Status.CANCELLED),
}


def can_view_request(user, request_obj):
    """Whether ``user`` may see ``request_obj`` (both parties + managers)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, request_obj.school):
        return False
    if user.is_superuser:
        return True
    if request_obj.assigned_to_id == user.pk:
        return True
    return user.has_role(*MANAGER_ROLES)


def can_respond(user, request_obj):
    """Whether ``user`` may make teacher-side changes to ``request_obj``."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, request_obj.school):
        return False
    if user.is_superuser:
        return True
    # The assigned teacher is the normal actor; a manager may also act on
    # behalf of a class that has no teacher yet.
    if request_obj.assigned_to_id == user.pk:
        return True
    return user.has_role(*MANAGER_ROLES)


def can_manage(user, request_obj):
    """Whether ``user`` may confirm / cancel / reopen ``request_obj``."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, request_obj.school):
        return False
    return bool(user.is_superuser or user.has_role(*MANAGER_ROLES))


def is_overdue(request_obj, today=None):
    """Convenience wrapper matching :attr:`UpdateRequest.is_overdue`."""
    return request_obj.is_overdue


def _log_event(request_obj, user, action, notes=''):
    UpdateRequestEvent.objects.create(
        request=request_obj, user=user, action=action, notes=notes or '')


def _audit(action, description, user, request_obj):
    AuditLog.log(
        action, description, user=user,
        object_type='academics.UpdateRequest', object_id=request_obj.pk)


@transaction.atomic
def create_update_request(request_user, *, school, title, category, description,
                          assigned_to, priority, due_date=None, academic_year=None,
                          term=None, school_class=None, subject=None,
                          student=None, assessment=None):
    """Create a request, record its history and notify the teacher."""
    if school is None:
        raise RequestError('A school is required to create a request.')
    if not can_access_school(request_user, school):
        raise RequestError('You cannot create requests for that school.')

    if assigned_to is None:
        raise RequestError('Choose the teacher responsible for this request.')
    if assigned_to.role != User.Role.TEACHER and not request_user.is_superuser:
        raise RequestError(
            'Requests must be assigned to a teacher or authorized staff member.')
    if assigned_to.school_id != school.pk:
        raise RequestError(
            'The assigned teacher must belong to the same school.')

    req = UpdateRequest.objects.create(
        school=school,
        title=title,
        category=category,
        description=description,
        requested_by=request_user,
        assigned_to=assigned_to,
        priority=priority,
        due_date=due_date,
        academic_year=academic_year,
        term=term,
        school_class=school_class,
        subject=subject,
        student=student,
        assessment=assessment,
    )
    _log_event(req, request_user, UpdateRequestEvent.Action.CREATED,
               f'Request created and assigned to {assigned_to.get_full_name() or assigned_to.username}.')
    _log_event(req, request_user, UpdateRequestEvent.Action.ASSIGNED,
               f'Assigned to {assigned_to.get_full_name() or assigned_to.username}.')
    _audit(AuditLog.ActionType.REQUEST_CREATED,
           f'Created update request "{title}" for {assigned_to} '
           f'({school.display_name}).', request_user, req)
    notifications.notify_request_created(req)
    return req


@transaction.atomic
def add_comment(request_obj, user, text):
    """Append a comment to a request's history (both parties may comment)."""
    text = (text or '').strip()
    if not text:
        raise RequestError('Comment cannot be empty.')
    if not can_view_request(user, request_obj):
        raise RequestError('You are not involved in this request.')
    _log_event(request_obj, user, UpdateRequestEvent.Action.COMMENTED, text)
    _audit(AuditLog.ActionType.REQUEST_COMMENTED,
           f'Commented on update request #{request_obj.pk}: {text[:120]}',
           user, request_obj)
    return request_obj


@transaction.atomic
def transition(request_obj, user, to_status, notes=''):
    """Move ``request_obj`` to ``to_status`` with full validation + history.

    Notes for submit/response transitions are also stored on ``response`` so
    the Head Teacher sees what the teacher did before confirming completion.
    """
    to_status = (to_status or '').strip().upper()
    if to_status not in dict(UpdateRequest.Status.choices):
        raise RequestError('Unknown request status.')

    from_status = request_obj.status
    if from_status == to_status:
        raise RequestError('The request is already in that state.')

    is_teacher_side = (request_obj.assigned_to_id == user.pk) or user.is_superuser
    allowed = set()
    if user.is_superuser or user.has_role(*MANAGER_ROLES):
        if can_access_school(user, request_obj.school):
            allowed |= MANAGER_TRANSITIONS
    if is_teacher_side and can_access_school(user, request_obj.school):
        allowed |= TEACHER_TRANSITIONS
    if not user.is_superuser and not user.has_role(*MANAGER_ROLES) \
            and not is_teacher_side:
        raise RequestError('You are not allowed to change this request.')

    if (from_status, to_status) not in allowed:
        raise RequestError(
            f'Cannot move a request from {request_obj.get_status_display()} '
            f'to {dict(UpdateRequest.Status.choices)[to_status]}.')

    request_obj.status = to_status
    notes = (notes or '').strip()
    if to_status == UpdateRequest.Status.SUBMITTED:
        request_obj.submitted_at = timezone.now()
        if notes:
            request_obj.response = notes
    elif to_status == UpdateRequest.Status.COMPLETED:
        request_obj.completed_at = timezone.now()
    elif to_status in (UpdateRequest.Status.PENDING,
                       UpdateRequest.Status.IN_PROGRESS):
        request_obj.completed_at = None
        request_obj.submitted_at = None

    action = {
        UpdateRequest.Status.IN_PROGRESS: UpdateRequestEvent.Action.STARTED,
        UpdateRequest.Status.SUBMITTED: UpdateRequestEvent.Action.SUBMITTED,
        UpdateRequest.Status.COMPLETED: UpdateRequestEvent.Action.COMPLETED,
        UpdateRequest.Status.CANCELLED: UpdateRequestEvent.Action.CANCELLED,
    }.get(to_status, UpdateRequestEvent.Action.REOPENED)

    request_obj.save(update_fields=[
        'status', 'response', 'submitted_at', 'completed_at', 'updated_at'])
    _log_event(request_obj, user, action, notes)
    _audit(
        AuditLog.ActionType.REQUEST_TRANSITIONED,
        f'Update request #{request_obj.pk} moved to '
        f'{request_obj.get_status_display()}.', user, request_obj)
    notifications.notify_request_transitioned(request_obj, user)
    return request_obj