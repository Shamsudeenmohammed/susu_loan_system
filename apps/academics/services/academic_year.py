"""Active academic-year resolution and exceptional overrides.

Every academic activity in a school happens inside exactly *one* active
academic year. This module is the single source of truth for that year:

* :func:`build_context` resolves the active year (and term) for the current
  request *on the server*. The client (a hidden field, a query string or a
  forged POST body) is never trusted to choose it.
* :func:`enforce_active_year` lets the write views reject a request that tries
  to work in an unauthorized year.
* :class:`~apps.academics.models.AcademicYearOverride` is an exceptional,
  reason-logged, time-boxed permission granted by an authorized Head Teacher or
  Admin that lets a scoped set of teachers work in a *different* year. It never
  bypasses school scoping, role rules or approval rules: it only changes which
  active year a teacher's records are attached to, and when it expires or is
  disabled the school's configured active year applies again.

Everything here is server-side. The UI only *reflects* this resolution; it can
never override it.
"""

from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.school_fees.models import AcademicYear, Term
from apps.schools.models import School
from apps.schools.scoping import (
    can_access_school,
    get_active_school,
    is_global_user,
    scope_queryset,
)

from ..models import AcademicYearOverride

# Roles allowed to enable/disable an exceptional override.
OVERRIDE_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')

# Resolution status codes.
STATUS_OK = 'ok'
STATUS_NONE = 'none'          # no active year configured for the school
STATUS_MULTIPLE = 'multiple'  # more than one active year - configuration problem
STATUS_COMBINED = 'combined'  # global user in the combined (all-schools) view
STATUS_NO_SCHOOL = 'no_school'

_UNSET = object()


class AcademicYearError(Exception):
    """Raised when an academic-year operation is not allowed."""


# ---------------------------------------------------------------------------
# Low-level lookups
# ---------------------------------------------------------------------------
def can_override(user):
    """Whether ``user`` may enable or disable an academic-year override."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    has_role = getattr(user, 'has_role', None)
    return bool(callable(has_role) and has_role(*OVERRIDE_ROLES))


def active_year_queryset(school):
    """The (single, ideally) active academic year(s) owned by ``school``."""
    if school is None:
        return AcademicYear.objects.none()
    return AcademicYear.objects.filter(
        school=school, is_active=True).order_by('-start_date', '-name')


def first_term(year):
    if year is None:
        return None
    return Term.objects.filter(academic_year=year).order_by('term_number').first()


def active_override_for_school(school):
    """The live override for ``school`` (disabled/expired ones excluded)."""
    if school is None:
        return None
    now = timezone.now()
    return (AcademicYearOverride.objects
            .filter(school=school, disabled_at__isnull=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related('academic_year', 'term', 'enabled_by')
            .order_by('-enabled_at')
            .first())


def override_applies_to(override, user):
    if override is None:
        return False
    if override.applies_to_all:
        return True
    return override.teachers.filter(pk=getattr(user, 'pk', None)).exists()


def resolve_school(school, user):
    """Return ``(year, term, override, status)`` for one school + user.

    An applicable live override wins; otherwise the school's single active year
    is used. ``status`` is ``STATUS_NONE`` when the school has no active year and
    ``STATUS_MULTIPLE`` when more than one is active (a configuration problem we
    surface instead of silently picking one).
    """
    override = active_override_for_school(school)
    if override is not None and override_applies_to(override, user):
        year = override.academic_year
        term = override.term or first_term(year)
        return year, term, override, STATUS_OK

    years = active_year_queryset(school)
    count = years.count()
    if count == 0:
        return None, None, None, STATUS_NONE
    if count > 1:
        return None, None, None, STATUS_MULTIPLE
    year = years.first()
    return year, first_term(year), None, STATUS_OK


# ---------------------------------------------------------------------------
# Request-scoped context
# ---------------------------------------------------------------------------
@dataclass
class AcademicContext:
    """The resolved academic year/term for a request, plus its provenance."""

    user: object = None
    school: object = None
    schools: list = field(default_factory=list)
    year: object = None
    term: object = None
    active_year: object = None
    active_term: object = None
    override: object = None
    status: str = STATUS_OK
    per_school: dict = field(default_factory=dict)

    @property
    def combined(self):
        return self.school is None and len(self.schools) > 1

    @property
    def is_override(self):
        return self.override is not None

    @property
    def has_year(self):
        return self.year is not None

    @property
    def label(self):
        if self.year is None:
            return ''
        text = self.year.name
        if self.term is not None:
            text = f'{text} · {self.term.name}'
        return text

    @property
    def school_name(self):
        return getattr(self.school, 'display_name', None) or 'your school'

    @property
    def error_message(self):
        if self.status == STATUS_NONE:
            return (
                f'No academic year is active for {self.school_name}. New '
                'academic records cannot be created until a Head Teacher or '
                'an Admin activates one.')
        if self.status == STATUS_MULTIPLE:
            return (
                f'More than one academic year is marked active for '
                f'{self.school_name}. Ask a Head Teacher or an Admin to leave '
                'exactly one active year before academic records are created.')
        if self.status == STATUS_NO_SCHOOL:
            return (
                'Your account is not linked to a school, so the active '
                'academic year cannot be resolved. Contact an Admin.')
        return ''


def build_context(request, school=_UNSET):
    """Resolve the active academic context for ``request`` (cached per request).

    * A school-scoped user resolves their own school.
    * A global user who switched into a school resolves that school.
    * A global user in the combined view gets no single year but a per-school
      map so each school's active year is resolved independently.
    """
    if school is _UNSET:
        cached = getattr(request, '_academic_context', None)
        if cached is not None:
            return cached

    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return AcademicContext(status=STATUS_NO_SCHOOL)

    if school is _UNSET:
        school = get_active_school(request)
    if school is None and not is_global_user(user):
        school = getattr(user, 'school', None)

    if school is not None:
        year, term, override, status = resolve_school(school, user)
        ctx = AcademicContext(
            user=user, school=school, schools=[school], year=year, term=term,
            active_year=year, active_term=term, override=override, status=status)
    elif is_global_user(user):
        schools = list(scope_queryset(
            School.objects.filter(is_active=True), request).order_by('name'))
        per_school = {}
        for candidate in schools:
            y, t, o, st = resolve_school(candidate, user)
            per_school[candidate.pk] = {
                'school': candidate, 'year': y, 'term': t, 'override': o,
                'status': st,
            }
        ctx = AcademicContext(
            user=user, school=None, schools=schools, status=STATUS_COMBINED,
            per_school=per_school)
    else:
        ctx = AcademicContext(user=user, status=STATUS_NO_SCHOOL)

    if school is _UNSET:
        request._academic_context = ctx
    return ctx


# ---------------------------------------------------------------------------
# Server-side enforcement
# ---------------------------------------------------------------------------
def effective_year_for(request, school=None):
    """Return the ``(year, term, context)`` a user must use for ``school``.

    For teacher-only users this is the school's resolved active year (honouring
    an override that applies to them). For reviewers/global users the supplied
    ``school`` (or the request's active school) is resolved the same way, but
    callers may still let them browse other years explicitly.
    """
    ctx = build_context(request, school if school is not None else _UNSET)
    return ctx.year, ctx.term, ctx


def enforce_active_year(request, *, school=None):
    """Return the active context for a teacher write, or raise.

    Raises :class:`AcademicYearError` when a teacher's school has no single
    active year (none, or the configuration problem of several). Reviewer and
    global users are never blocked here - they may legitimately work across
    years - but the school scoping in the views still applies.
    """
    from . import access

    ctx = build_context(request, school if school is not None else _UNSET)
    if not access.is_teacher_only(request.user):
        return ctx
    if ctx.status in (STATUS_NONE, STATUS_MULTIPLE, STATUS_NO_SCHOOL):
        raise AcademicYearError(ctx.error_message or 'No academic year is active.')
    return ctx


def assert_year_allowed(request, year, *, school=None):
    """Reject a teacher submission that targets another academic year.

    Returns ``True`` when ``year`` matches the teacher's resolved active year.
    Reviewers/global users always pass (their scoping is enforced elsewhere).
    """
    from . import access

    if not access.is_teacher_only(request.user):
        return True
    ctx = build_context(request, school if school is not None else _UNSET)
    if ctx.year is None or year is None:
        return False
    return ctx.year.pk == getattr(year, 'pk', year)


# ---------------------------------------------------------------------------
# Enable / disable an override (audited)
# ---------------------------------------------------------------------------
def enable_override(*, user, school, academic_year, reason, expires_at=None,
                    term=None, applies_to_all=True, teachers=None):
    """Open an exceptional override. Raises :class:`AcademicYearError` on abuse."""
    if not can_override(user):
        raise AcademicYearError(
            'You are not allowed to enable an academic-year override.')
    if school is None or academic_year is None:
        raise AcademicYearError('A school and an academic year are required.')
    if not can_access_school(user, school):
        raise AcademicYearError('That school is outside your scope.')
    if not (reason or '').strip():
        raise AcademicYearError('A reason is required to enable an override.')
    if getattr(academic_year, 'school_id', None) != school.pk:
        raise AcademicYearError(
            'The academic year does not belong to the selected school.')
    if term is not None and term.academic_year_id != academic_year.pk:
        raise AcademicYearError(
            'The term does not belong to the selected academic year.')
    if active_override_for_school(school) is not None:
        raise AcademicYearError(
            'An override is already active for this school. Disable it before '
            'opening another.')

    override = AcademicYearOverride.objects.create(
        school=school, academic_year=academic_year, term=term,
        reason=reason.strip(), enabled_by=user, expires_at=expires_at,
        applies_to_all=applies_to_all)
    if not applies_to_all:
        override.teachers.set(list(teachers or []))
    AuditLog.log(
        AuditLog.ActionType.OVERRIDE_ENABLED,
        f'Academic-year override enabled for {school} to {academic_year.name}'
        + (f' until {expires_at.isoformat()}' if expires_at else ' (no expiry)')
        + f'. Reason: {override.reason}',
        user=user, object_type='AcademicYearOverride', object_id=override.pk)
    return override


def disable_override(override, *, user, reason=''):
    """Close an override, recording who disabled it and when."""
    if not can_override(user):
        raise AcademicYearError(
            'You are not allowed to disable an academic-year override.')
    if not can_access_school(user, override.school):
        raise AcademicYearError('That school is outside your scope.')
    if override.disabled_at is not None:
        raise AcademicYearError('That override is already disabled.')

    override.disabled_at = timezone.now()
    override.disabled_by = user
    override.disabled_reason = (reason or '').strip()
    override.save(update_fields=['disabled_at', 'disabled_by', 'disabled_reason'])
    AuditLog.log(
        AuditLog.ActionType.OVERRIDE_DISABLED,
        f'Academic-year override for {override.school} to '
        f'{override.academic_year.name} disabled'
        + (f'. Reason: {override.disabled_reason}' if override.disabled_reason else '.'),
        user=user, object_type='AcademicYearOverride', object_id=override.pk)
    return override
