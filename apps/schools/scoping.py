"""Helpers for enforcing school-level data isolation.

The rules implemented here are deliberately backward compatible:

* SUPER_ADMIN / ADMIN / PROPRIETOR (and Django superusers) are *global* users.
  They can see every school. A global user may optionally "switch" into a
  single school (stored in the session) to narrow the views to that school.
* Any other staff member who has a ``school`` assigned is scoped to that school
  only. Records with no school (legacy rows created before multi-school) are
  invisible to school-scoped users.
* Staff members with no school assigned keep their original global behaviour so
  the existing Susu Savings & Loans workflows are unaffected.
"""

from django.db.models import Q

from .models import School

# Roles that always operate across every school.
GLOBAL_ROLES = {'SUPER_ADMIN', 'ADMIN', 'PROPRIETOR'}

# Roles that must always be tied to exactly one school.
SCHOOL_BOUND_ROLES = {'HEAD_TEACHER', 'TEACHER'}

SESSION_KEY = 'active_school_id'


def is_global_user(user):
    """Return True when the user is allowed to operate across schools."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    role = getattr(user, 'role', None)
    if role in GLOBAL_ROLES:
        return True
    if role in SCHOOL_BOUND_ROLES:
        return False
    # Regular staff keep global access unless explicitly assigned to a school.
    return getattr(user, 'school_id', None) is None


def is_school_user(user):
    """Return True when the user is scoped to a single school."""
    return getattr(user, 'is_authenticated', False) and not is_global_user(user)


def user_school(user):
    """The fixed school for a school-scoped user (None for global users)."""
    if is_global_user(user):
        return None
    return getattr(user, 'school', None)


def can_access_school(user, school):
    """Whether ``user`` may view/act on data belonging to ``school``."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if is_global_user(user):
        return True
    if school is None:
        return False
    return getattr(user, 'school_id', None) == getattr(school, 'pk', school)


def can_access_object(user, obj, field='school'):
    """Object-level check based on the object's ``school`` attribute."""
    return can_access_school(user, getattr(obj, field, None))


def get_active_school(request):
    """The school currently in focus for ``request``.

    * School-scoped users always get their own school.
    * Global users get the school they switched into, or ``None`` (meaning
      "all schools") when they have not switched.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return None
    if is_global_user(user):
        sid = request.session.get(SESSION_KEY) if hasattr(request, 'session') else None
        if sid:
            return School.objects.filter(pk=sid, is_active=True).first()
        return None
    return getattr(user, 'school', None)


def scope_queryset(queryset, request, field='school'):
    """Filter ``queryset`` down to the schools ``request`` may access.

    Global users with no active school see everything (combined view). Global
    users who switched into a school, and all school-scoped users, only see
    their school. School-scoped users without a school see nothing.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return queryset.none()

    if is_global_user(user):
        school = get_active_school(request)
        if school is None:
            return queryset
        return queryset.filter(**{field: school})

    if getattr(user, 'school_id', None) is None:
        return queryset.none()
    return queryset.filter(**{field: user.school})


def with_shared(queryset, school, field='school'):
    """Return rows belonging to ``school`` plus organisation-wide reference rows.

    Shared reference data (academic years, terms, fee categories) may be defined
    once for the whole organisation, in which case ``field`` is ``NULL``. Those
    rows must stay selectable by every branch, alongside the branch's own rows.
    Isolation still holds: another branch's rows are never included.
    """
    return queryset.filter(Q(**{field: school}) | Q(**{f'{field}__isnull': True}))


def scope_reference_queryset(queryset, request, field='school'):
    """Like :func:`scope_queryset`, but keeps organisation-wide rows visible.

    Use this for shared reference data (years, terms, categories) so a
    school-bound user can still select the shared academic calendar instead of
    seeing empty dropdowns. Operational/owned data (students, payments) must keep
    using :func:`scope_queryset`.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return queryset.none()

    if is_global_user(user):
        school = get_active_school(request)
        if school is None:
            return queryset
        return with_shared(queryset, school, field)

    if getattr(user, 'school_id', None) is None:
        return queryset.none()
    return with_shared(queryset, user.school, field)


def scope_queryset_by_path(queryset, request, path):
    """Like :func:`scope_queryset` but filters on a related path.

    ``path`` is a Django ORM lookup such as ``student__school``.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return queryset.none()

    if is_global_user(user):
        school = get_active_school(request)
        if school is None:
            return queryset
        return queryset.filter(**{path: school})

    if getattr(user, 'school_id', None) is None:
        return queryset.none()
    return queryset.filter(**{path: user.school})
