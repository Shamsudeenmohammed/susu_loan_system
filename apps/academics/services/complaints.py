"""Teacher-raised complaints, requests and safeguarding concerns.

Any staff member may raise a concern at any time. Management (Head Teacher /
Admin) follows it up to resolution, recording assignment, comments, due dates,
action history and resolution notes. A safeguarding case or an urgent priority
case is escalated immediately on creation.

Statuses: ``SUBMITTED``, ``ACKNOWLEDGED``, ``IN_PROGRESS``, ``RESOLVED`` and
``REOPENED``.

Nothing here ever crosses a school boundary: every case is a
``SchoolOwnedModel`` and the views query them through the school-scoping helper.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.schools.scoping import can_access_school

from ..models import Complaint, ComplaintEvent
from . import notifications

#: Roles that manage (triage / resolve) concerns.
MANAGER_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')

#: Statuses whose due dates are still being tracked.
OPEN_STATUSES = (Complaint.Status.SUBMITTED, Complaint.Status.ACKNOWLEDGED,
                 Complaint.Status.IN_PROGRESS, Complaint.Status.REOPENED)

#: Allowed ``(from_status, to_status)`` pairs for management.
MANAGER_TRANSITIONS = {
    (Complaint.Status.SUBMITTED, Complaint.Status.ACKNOWLEDGED),
    (Complaint.Status.SUBMITTED, Complaint.Status.IN_PROGRESS),
    (Complaint.Status.SUBMITTED, Complaint.Status.RESOLVED),
    (Complaint.Status.ACKNOWLEDGED, Complaint.Status.IN_PROGRESS),
    (Complaint.Status.ACKNOWLEDGED, Complaint.Status.RESOLVED),
    (Complaint.Status.IN_PROGRESS, Complaint.Status.RESOLVED),
    (Complaint.Status.REOPENED, Complaint.Status.ACKNOWLEDGED),
    (Complaint.Status.REOPENED, Complaint.Status.IN_PROGRESS),
    (Complaint.Status.REOPENED, Complaint.Status.RESOLVED),
}

#: Allowed ``(from_status, to_status)`` pairs for the person who raised it.
RAISER_TRANSITIONS = {
    (Complaint.Status.RESOLVED, Complaint.Status.REOPENED),
}


class ComplaintError(Exception):
    """Raised when a complaint operation is not allowed."""


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def can_view(user, complaint):
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, complaint.school):
        return False
    if user.is_superuser:
        return True
    if complaint.raised_by_id == user.pk:
        return True
    if complaint.assigned_to_id == user.pk:
        return True
    return user.has_role(*MANAGER_ROLES)


def can_manage(user, complaint):
    """Whether ``user`` may triage/resolve a concern."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if not can_access_school(user, complaint.school):
        return False
    return bool(user.is_superuser or user.has_role(*MANAGER_ROLES))


def can_reopen(user, complaint):
    if not can_view(user, complaint):
        return False
    return bool(user.is_superuser
                or complaint.raised_by_id == user.pk
                or user.has_role(*MANAGER_ROLES))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _log_event(complaint, user, action, notes=''):
    ComplaintEvent.objects.create(
        complaint=complaint, user=user, action=action, notes=notes or '')


def _audit(action, description, user, complaint):
    AuditLog.log(
        action, description, user=user,
        object_type='academics.Complaint', object_id=complaint.pk)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
@transaction.atomic
def create_complaint(raiser, *, school, title, description, case_type,
                     category, priority=Complaint.Priority.MEDIUM,
                     supporting_details='', school_class=None, student=None,
                     assigned_to=None, due_date=None):
    """Raise a concern and notify management (immediately if escalated)."""
    if not getattr(raiser, 'is_authenticated', False):
        raise ComplaintError('You must be signed in to raise a concern.')
    if school is None:
        raise ComplaintError('A school is required.')
    if not can_access_school(raiser, school):
        raise ComplaintError('You cannot raise a concern for that school.')
    title = (title or '').strip()
    description = (description or '').strip()
    if not title:
        raise ComplaintError('Give the concern a short title.')
    if not description:
        raise ComplaintError('Describe the concern.')
    if case_type not in dict(Complaint.CaseType.choices):
        raise ComplaintError('Unknown concern type.')
    if category not in dict(Complaint.Category.choices):
        raise ComplaintError('Unknown category.')

    if school_class is not None and school_class.school_id != school.pk:
        raise ComplaintError('The class must belong to the same school.')
    if student is not None and student.school_id != school.pk:
        raise ComplaintError('The student must belong to the same school.')
    if assigned_to is not None and getattr(assigned_to, 'school_id', None) != school.pk:
        raise ComplaintError('The assignee must belong to the same school.')

    complaint = Complaint.objects.create(
        school=school,
        title=title,
        description=description,
        case_type=case_type,
        category=category,
        priority=priority,
        supporting_details=(supporting_details or '').strip(),
        school_class=school_class,
        student=student,
        raised_by=raiser,
        assigned_to=assigned_to,
        due_date=due_date,
    )
    _log_event(complaint, raiser, ComplaintEvent.Action.CREATED,
               'Concern raised.')
    if complaint.is_escalated:
        _log_event(complaint, raiser, ComplaintEvent.Action.ESCALATED,
                   'Escalated immediately (safeguarding / urgent).')
    if assigned_to is not None:
        _log_event(complaint, raiser, ComplaintEvent.Action.ASSIGNED,
                   f'Assigned to {assigned_to.get_full_name() or assigned_to.username}.')
    _audit(
        AuditLog.ActionType.COMPLAINT_CREATED,
        f'{complaint.get_case_type_display()} "{complaint.title}" raised '
        f'({complaint.get_priority_display()} priority, '
        f'{school.display_name}).', raiser, complaint)
    notifications.notify_complaint_created(complaint)
    return complaint


@transaction.atomic
def assign(complaint, user, assigned_to):
    """Assign or reassign a concern to a responsible staff member."""
    if not can_manage(user, complaint):
        raise ComplaintError('You are not allowed to assign this concern.')
    if assigned_to is not None and getattr(assigned_to, 'school_id', None) != complaint.school_id:
        raise ComplaintError('The assignee must belong to the same school.')
    complaint.assigned_to = assigned_to
    complaint.save(update_fields=['assigned_to', 'updated_at'])
    name = (assigned_to.get_full_name() or assigned_to.username) if assigned_to else 'nobody'
    _log_event(complaint, user, ComplaintEvent.Action.ASSIGNED, f'Assigned to {name}.')
    return complaint


@transaction.atomic
def add_comment(complaint, user, text):
    """Append a comment/response to a concern's action history."""
    text = (text or '').strip()
    if not text:
        raise ComplaintError('Comment cannot be empty.')
    if not can_view(user, complaint):
        raise ComplaintError('You are not involved in this concern.')
    _log_event(complaint, user, ComplaintEvent.Action.COMMENTED, text)
    _audit(
        AuditLog.ActionType.COMPLAINT_COMMENTED,
        f'Commented on concern #{complaint.pk}: {text[:120]}', user, complaint)
    return complaint


@transaction.atomic
def transition(complaint, user, to_status, notes=''):
    """Move a concern to ``to_status`` with validation, history and audit."""
    to_status = (to_status or '').strip().upper()
    if to_status not in dict(Complaint.Status.choices):
        raise ComplaintError('Unknown concern status.')

    from_status = complaint.status
    if from_status == to_status:
        raise ComplaintError('The concern is already in that state.')

    is_manager = can_manage(user, complaint)
    is_raiser = can_access_school(user, complaint.school) and (
        complaint.raised_by_id == user.pk)

    allowed = set()
    if is_manager:
        allowed |= MANAGER_TRANSITIONS
    if is_raiser:
        allowed |= RAISER_TRANSITIONS

    if not is_manager and not is_raiser:
        raise ComplaintError('You are not allowed to change this concern.')
    if (from_status, to_status) not in allowed:
        raise ComplaintError(
            f'Cannot move a concern from {complaint.get_status_display()} '
            f'to {dict(Complaint.Status.choices)[to_status]}.')

    notes = (notes or '').strip()
    complaint.status = to_status
    if to_status == Complaint.Status.ACKNOWLEDGED:
        complaint.acknowledged_at = timezone.now()
    elif to_status == Complaint.Status.RESOLVED:
        complaint.resolved_at = timezone.now()
        if notes:
            complaint.resolution_notes = notes
    elif to_status == Complaint.Status.REOPENED:
        complaint.resolved_at = None

    action = {
        Complaint.Status.ACKNOWLEDGED: ComplaintEvent.Action.ACKNOWLEDGED,
        Complaint.Status.IN_PROGRESS: ComplaintEvent.Action.STARTED,
        Complaint.Status.RESOLVED: ComplaintEvent.Action.RESOLVED,
        Complaint.Status.REOPENED: ComplaintEvent.Action.REOPENED,
    }.get(to_status, ComplaintEvent.Action.COMMENTED)
    complaint.save()
    _log_event(complaint, user, action, notes)
    _audit(
        AuditLog.ActionType.COMPLAINT_TRANSITIONED,
        f'Concern #{complaint.pk} moved to {complaint.get_status_display()}.',
        user, complaint)
    notifications.notify_complaint_transitioned(complaint, user)
    return complaint
