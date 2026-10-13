"""Access-control helpers for the academics module.

Every view that touches academic data checks these helpers in addition to the
queryset scoping in :mod:`apps.schools.scoping`. A teacher is never trusted
from the frontend: class/subject permissions are derived from
:class:`~apps.academics.models.TeacherAssignment` on the server.
"""

from django.db.models import Q

from apps.school_fees.models import SchoolClass
from apps.schools.scoping import can_access_school, scope_queryset

from ..models import ClassSubject, TeacherAssignment

CONFIG_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')
REVIEW_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')


def is_teacher_only(user):
    return getattr(user, 'role', None) == 'TEACHER'


def can_configure(user):
    """Whether the user may configure academic structure/assessments."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_superuser or user.has_role(*CONFIG_ROLES))


def can_review(user):
    """Whether the user may approve/return submissions and publish results."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_superuser or user.has_role(*REVIEW_ROLES))


def teacher_class_ids(user, year=None):
    qs = TeacherAssignment.objects.filter(teacher=user, is_active=True)
    if year is not None:
        qs = qs.filter(academic_year=year)
    return qs.values_list('school_class_id', flat=True).distinct()


def teacher_class_subject_pairs(user, year=None, term=None):
    """Map ``class_id -> {subject_id, ...}`` the teacher is assigned to."""
    qs = TeacherAssignment.objects.filter(teacher=user, is_active=True)
    if year is not None:
        qs = qs.filter(academic_year=year)
    if term is not None:
        qs = qs.filter(Q(term=term) | Q(term__isnull=True))
    pairs = {}
    for class_id, subject_id in qs.values_list('school_class_id', 'subject_id'):
        pairs.setdefault(class_id, set()).add(subject_id)
    return pairs


def can_edit_class_subject(user, school_class, subject, year=None, term=None):
    """True when ``user`` may enter marks for a class/subject."""
    if school_class is None or subject is None:
        return False
    if not can_access_school(user, school_class.school):
        return False
    if not is_teacher_only(user):
        return True
    qs = TeacherAssignment.objects.filter(
        teacher=user, school_class=school_class, subject=subject, is_active=True)
    if year is not None:
        qs = qs.filter(academic_year=year)
    if term is not None:
        qs = qs.filter(Q(term=term) | Q(term__isnull=True))
    return qs.exists()


def can_take_attendance(user, school_class, subject=None, year=None, term=None):
    """True when ``user`` may record attendance for a class (and subject)."""
    if school_class is None:
        return False
    if not can_access_school(user, school_class.school):
        return False
    if not is_teacher_only(user):
        return True
    qs = TeacherAssignment.objects.filter(
        teacher=user, school_class=school_class, is_active=True)
    if year is not None:
        qs = qs.filter(academic_year=year)
    if term is not None:
        qs = qs.filter(Q(term=term) | Q(term__isnull=True))
    if subject is not None:
        qs = qs.filter(subject=subject)
    return qs.exists()


def visible_classes(request, year=None):
    """Active, school-scoped classes the request user may see."""
    qs = scope_queryset(SchoolClass.objects.filter(is_active=True), request)
    if is_teacher_only(request.user):
        qs = qs.filter(pk__in=list(teacher_class_ids(request.user, year)))
    return qs.order_by('name')


def visible_class_subjects(request, school_class=None, year=None, term=None):
    """Active, scoped class-subjects, narrowed for teachers to their own."""
    qs = scope_queryset(ClassSubject.objects.filter(is_active=True), request)
    if school_class is not None:
        qs = qs.filter(school_class=school_class)
    if is_teacher_only(request.user):
        pairs = teacher_class_subject_pairs(request.user, year, term)
        allowed = [
            cs_pk for cs_pk, class_id, subject_id in qs.values_list(
                'pk', 'school_class_id', 'subject_id')
            if subject_id in pairs.get(class_id, set())
        ]
        qs = qs.filter(pk__in=allowed)
    return qs.select_related('school_class', 'subject').order_by(
        'school_class__name', 'subject__name')
