"""Academic management views.

Every view is school-scoped on the server via :mod:`apps.schools.scoping` and,
for teachers, further restricted through
:mod:`apps.academics.services.access` using their ``TeacherAssignment`` rows.
"""

from collections import Counter
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Exists, F, OuterRef, Q, Sum, Value
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.decorators import role_required
from apps.school_fees.models import (
    AcademicYear,
    SchoolClass,
    Student,
    StudentFeeAccount,
    Term,
)
from apps.schools.models import School
from apps.schools.scoping import (
    can_access_school,
    get_active_school,
    is_global_user,
    scope_queryset,
    scope_reference_queryset,
)

from .forms import (
    AcademicCalendarEventForm,
    AcademicSettingsForm,
    AcademicYearOverrideForm,
    AnnouncementForm,
    AssessmentForm,
    ClassSubjectForm,
    GradingScaleForm,
    GradeBandFormSet,
    MarkCorrectionReasonForm,
    PromotionForm,
    SubjectForm,
    TeacherAssignmentForm,
    TerminalCommentForm,
    TerminalReportFieldFormSet,
    UpdateRequestCommentForm,
    UpdateRequestForm,
    UpdateRequestResponseForm,
)
from .models import (
    AcademicCalendarEvent,
    AcademicSettings,
    AcademicYearOverride,
    Announcement,
    Assessment,
    AttendanceRecord,
    AttendanceSession,
    ClassSubject,
    GradeBand,
    GradingScale,
    MarkCorrection,
    MarkSubmission,
    PromotionRecord,
    ResultPublication,
    StudentMark,
    Subject,
    TeacherAssignment,
    TerminalComment,
    TerminalReportField,
    UpdateRequest,
    UpdateRequestEvent,
)
from .services import (
    access,
    academic_year as academic_year_service,
    attendance as attendance_service,
    marks,
    reports,
    requests as requests_service,
)
from .services import results as results_service
from .services.grading import resolve_scale, subject_result as grading_subject_result

ACADEMIC_ROLES = ('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'PROPRIETOR', 'HEAD_TEACHER', 'TEACHER')
REVIEW_ROLES = ('SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scope(request, queryset):
    return scope_queryset(queryset, request)


def _denied(request, obj, field='school'):
    if not can_access_school(request.user, getattr(obj, field, None)):
        messages.error(
            request,
            'That record belongs to a different school. You can only manage '
            'records for your assigned school.')
        return True
    return False


def _select_year_term(request):
    """Pick the academic year/term for a view from the request (or defaults)."""
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    year = None
    year_id = request.GET.get('academic_year') or request.POST.get('academic_year')
    if year_id:
        year = years.filter(pk=year_id).first()
    if year is None:
        year = years.first()

    term = None
    terms = Term.objects.none()
    if year is not None:
        terms = Term.objects.filter(academic_year=year).order_by('term_number')
        term_id = request.GET.get('term') or request.POST.get('term')
        if term_id:
            term = terms.filter(pk=term_id).first()
        if term is None:
            term = terms.first()
    return year, term, years, terms


def _academic_context(request, school=None):
    """The resolved active-year context for the request (server-authoritative)."""
    if school is not None:
        return academic_year_service.build_context(request, school)
    return academic_year_service.build_context(request)


def _active_year_term(request, *, school=None):
    """Year/term a view must use, forcing the active year for teachers.

    For a teacher the request's year/term selectors are ignored entirely: the
    school's active year (or an authorised override) wins, so a forged GET/POST
    value can never move them into another year. Reviewers/global users keep
    the explicit, browsable selection.
    """
    ctx = _academic_context(request, school)
    if access.is_teacher_only(request.user):
        return ctx.year, ctx.term, ctx
    year, term, _years, _terms = _select_year_term(request)
    return year, term, ctx


def _class_choices(request):
    return access.visible_classes(request)


def _paginate(request, queryset, per_page=20, param='page'):
    """Return a ``Page`` for ``queryset`` using the ``?page=`` parameter."""
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get(param))


def _get_str(request, key, default=''):
    return (request.GET.get(key) or default).strip()


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def home(request):
    """Route the user to the academic dashboard appropriate to their role."""
    if access.is_teacher_only(request.user):
        return redirect('academics:teacher_dashboard')
    return redirect('academics:head_dashboard')


def scope_assigned_teachers(request):
    """Teachers visible to the request (school-scoped)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    teachers = User.objects.filter(role='TEACHER', is_active=True)
    school = get_active_school(request)
    if school is not None:
        return teachers.filter(school=school)
    if not access.is_teacher_only(request.user) and getattr(request.user, 'school_id', None):
        return teachers.filter(school=request.user.school)
    return teachers


def _submission_tone(status):
    return {
        MarkSubmission.Status.APPROVED: 'success',
        MarkSubmission.Status.SUBMITTED: 'warning',
        MarkSubmission.Status.RETURNED: 'danger',
        MarkSubmission.Status.DRAFT: 'neutral',
    }.get(status, 'neutral')


@login_required
@role_required(*ACADEMIC_ROLES)
def teacher_dashboard(request):
    user = request.user
    academic_context = _academic_context(request)
    year = academic_context.year
    term = academic_context.term
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    terms = (Term.objects.filter(academic_year=year).order_by('term_number')
             if year is not None else Term.objects.none())
    assignments = TeacherAssignment.objects.filter(
        teacher=user, is_active=True).select_related(
        'school_class', 'subject', 'academic_year', 'term')
    if year is not None:
        assignments = assignments.filter(academic_year=year)

    class_ids = list(assignments.values_list('school_class_id', flat=True).distinct())
    subject_ids = list(assignments.values_list('subject_id', flat=True).distinct())

    assessments = Assessment.objects.filter(
        school_class_id__in=class_ids, subject_id__in=subject_ids,
        is_active=True)
    if year is not None:
        assessments = assessments.filter(academic_year=year)
    if term is not None:
        assessments = assessments.filter(term=term)

    # Assessments that still have unentered marks, plus a completeness figure.
    pending = []
    complete_count = 0
    for assessment in assessments.select_related('school_class', 'subject'):
        missing = marks.missing_mark_count(assessment)
        if missing:
            pending.append({'assessment': assessment, 'missing': missing})
        else:
            complete_count += 1
    assessment_total = len(pending) + complete_count

    submissions = list(MarkSubmission.objects.filter(
        submitted_by=user).select_related(
        'school_class', 'subject', 'academic_year', 'term').order_by('-updated_at')[:10])
    submission_stats = {
        'total': len(submissions),
        'approved': sum(1 for s in submissions if s.status == MarkSubmission.Status.APPROVED),
        'submitted': sum(1 for s in submissions if s.status == MarkSubmission.Status.SUBMITTED),
        'returned': sum(1 for s in submissions if s.status == MarkSubmission.Status.RETURNED),
    }
    for submission in submissions:
        submission.tone = _submission_tone(submission.status)

    announcements = Announcement.objects.none()
    school = get_active_school(request) or getattr(user, 'school', None)
    if school is not None:
        announcements = Announcement.objects.filter(
            school=school, is_published=True,
            audience__in=['ALL', 'TEACHERS']).order_by('-published_at')[:5]
    events = AcademicCalendarEvent.objects.none()
    if school is not None:
        events = _scope(request, AcademicCalendarEvent.objects.filter(
            start_date__gte=timezone.localdate())).order_by('start_date')[:5]

    # Requests assigned to this teacher.
    my_requests = _scope(request, UpdateRequest.objects.filter(
        assigned_to=user)).select_related('school_class', 'subject')
    my_open_count = my_requests.filter(
        status__in=requests_service.OPEN_STATUSES).count()
    my_submitted_count = my_requests.filter(
        status=UpdateRequest.Status.SUBMITTED).count()
    my_overdue_count = my_requests.filter(
        status__in=requests_service.OPEN_STATUSES,
        due_date__lt=timezone.localdate()).count()
    recent_requests = list(my_requests.order_by('-created_at')[:8])
    for req in recent_requests:
        req.status_tone = _request_status_tone(req.status)

    stats = [
        {'label': 'My classes', 'value': len(class_ids), 'icon': 'fa-chalkboard',
         'tone': 'teal', 'hint': 'Assigned this year',
         'url': reverse('academics:assessment_list')},
        {'label': 'My subjects', 'value': len(subject_ids), 'icon': 'fa-book',
         'tone': 'violet', 'hint': 'Across my classes',
         'url': reverse('academics:assessment_list')},
        {'label': 'Marks still to enter', 'value': len(pending), 'icon': 'fa-pen-to-square',
         'tone': 'amber', 'hint': f'{assessment_total} assessment(s) in total',
         'url': reverse('academics:assessment_list')},
        {'label': 'My submissions', 'value': submission_stats['total'], 'icon': 'fa-paper-plane',
         'tone': 'slate', 'hint': f"{submission_stats['submitted']} awaiting review",
         'url': reverse('academics:submissions')},
        {'label': 'Open requests', 'value': my_open_count, 'icon': 'fa-list-check',
         'tone': 'rose', 'hint': f'{my_overdue_count} overdue · {my_submitted_count} submitted',
         'url': reverse('academics:update_request_list')},
    ]
    quick_actions = [
        {'label': 'Take attendance', 'icon': 'fa-clipboard-check', 'tone': 'teal',
         'url': reverse('academics:attendance_index')},
        {'label': 'New assessment', 'icon': 'fa-file-pen', 'tone': '',
         'url': reverse('academics:assessment_create')},
        {'label': 'My submissions', 'icon': 'fa-paper-plane', 'tone': 'violet',
         'url': reverse('academics:submissions')},
        {'label': 'Academic calendar', 'icon': 'fa-calendar-days', 'tone': 'amber',
         'url': reverse('academics:calendar')},
    ]
    progress_chart = {
        'type': 'doughnut',
        'labels': ['Marks complete', 'Marks pending'],
        'datasets': [{
            'data': [complete_count, len(pending)],
            'backgroundColor': ['#0d9488', '#f59e0b'],
            'borderWidth': 0,
        }],
    }

    return render(request, 'academics/dashboard_teacher.html', {
        'year': year, 'term': term, 'years': years, 'terms': terms,
        'academic_context': academic_context,
        'assignments': assignments,
        'class_count': len(class_ids),
        'subject_count': len(subject_ids),
        'pending_marks': pending[:25],
        'pending_count': len(pending),
        'assessment_total': assessment_total,
        'complete_count': complete_count,
        'submissions': submissions,
        'submission_stats': submission_stats,
        'my_requests': recent_requests,
        'my_open_count': my_open_count,
        'my_submitted_count': my_submitted_count,
        'my_overdue_count': my_overdue_count,
        'announcements': announcements,
        'events': events,
        'stats': stats,
        'quick_actions': quick_actions,
        'progress_chart': progress_chart,
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def head_dashboard(request):
    school = get_active_school(request) or getattr(request.user, 'school', None)
    year, term, years, terms = _select_year_term(request)
    academic_context = _academic_context(request)
    can_override = academic_year_service.can_override(request.user)
    active_override = academic_context.override
    if school is None:
        # Combined view: surface any per-school overrides for the banner.
        live_overrides = [entry['override'] for entry in academic_context.per_school.values()
                          if entry.get('override') is not None]
    else:
        live_overrides = [active_override] if active_override else []

    students = _scope(request, Student.objects.filter(is_active=True))
    classes = _scope(request, SchoolClass.objects.filter(is_active=True))
    subjects = _scope(request, Subject.objects.filter(is_active=True))
    teachers = scope_assigned_teachers(request)

    student_count = students.count()
    class_count = classes.count()
    subject_count = subjects.count()
    teacher_count = teachers.count()

    submissions_all = _scope(request, MarkSubmission.objects.all())
    pending_submissions = _scope(request, MarkSubmission.objects.filter(
        status=MarkSubmission.Status.SUBMITTED)).select_related(
        'school_class', 'subject', 'submitted_by', 'term', 'academic_year')
    pending_count = pending_submissions.count()
    returned_count = submissions_all.filter(
        status=MarkSubmission.Status.RETURNED).count()
    approved_count = submissions_all.filter(
        status=MarkSubmission.Status.APPROVED).count()

    # Submissions approved but whose class/term is not yet published.
    published_block = ResultPublication.objects.filter(
        academic_year=OuterRef('academic_year'), term=OuterRef('term'),
        school_class=OuterRef('school_class'), is_published=True)
    ready_to_publish = submissions_all.filter(
        status=MarkSubmission.Status.APPROVED).annotate(
        is_published_block=Exists(published_block)).filter(
        is_published_block=False).count()

    # Requests & follow-ups.
    requests_qs = _scope(request, UpdateRequest.objects.all())
    open_requests = requests_qs.filter(status__in=requests_service.OPEN_STATUSES)
    open_request_count = open_requests.count()
    overdue_request_count = open_requests.filter(
        due_date__lt=timezone.localdate()).count()
    recent_open_requests = list(open_requests.select_related(
        'assigned_to', 'school_class', 'subject').order_by('-created_at')[:8])
    for req in recent_open_requests:
        req.status_tone = _request_status_tone(req.status)

    attendance_rows = []
    overall_attendance = None
    if year is not None and term is not None:
        total_attended = total_sessions = 0
        for school_class in classes:
            summary = attendance_service.class_summary(school_class, year, term)
            if summary:
                attended = sum(r['attended'] for r in summary)
                total = sum(r['total'] for r in summary)
                pct = (Decimal(attended) / Decimal(total) * 100).quantize(Decimal('0.1')) if total else None
                attendance_rows.append({'school_class': school_class, 'percentage': pct})
                total_attended += attended
                total_sessions += total
        if total_sessions:
            overall_attendance = (
                Decimal(total_attended) / Decimal(total_sessions) * 100
            ).quantize(Decimal('0.1'))
    classes_without_attendance = max(class_count - len(attendance_rows), 0)

    # Read-only fee summary for the selected period (real ledger aggregates).
    fee_summary = None
    if year is not None and term is not None:
        totals = _scope(request, StudentFeeAccount.objects.filter(
            academic_year=year, term=term)).aggregate(
            charged=Sum('total_fees'), paid=Sum('amount_paid'))
        charged = totals['charged'] or Decimal('0')
        paid = totals['paid'] or Decimal('0')
        fee_summary = {'charged': charged, 'paid': paid,
                       'outstanding': charged - paid}

    # Class performance = average of recorded marks (as % of each maximum).
    class_performance = []
    if year is not None and term is not None:
        per_class = {}
        mark_rows = StudentMark.objects.filter(
            assessment__school_class__in=classes,
            assessment__academic_year=year, assessment__term=term,
            score__isnull=False, assessment__max_score__gt=0).values_list(
            'assessment__school_class_id', 'score', 'assessment__max_score')
        for class_id, score, max_score in mark_rows:
            per_class.setdefault(class_id, []).append(
                Decimal(score) / Decimal(max_score) * 100)
        ordered = sorted(classes, key=lambda c: c.name)
        for school_class in ordered:
            values = per_class.get(school_class.pk)
            average = None
            if values:
                raw = sum(values) / Decimal(len(values))
                average = raw.quantize(Decimal('0.1'))
            class_performance.append(
                {'school_class': school_class, 'average': average})
        class_performance.sort(
            key=lambda row: (row['average'] is None,
                             -(row['average'] or 0)))

    publications = list(_scope(request, ResultPublication.objects.filter(
        is_published=True)).select_related('school_class', 'term')[:10])

    announcements = Announcement.objects.none()
    if school is not None:
        announcements = Announcement.objects.filter(
            school=school, is_published=True).order_by('-published_at')[:5]
    events = AcademicCalendarEvent.objects.none()
    if school is not None:
        events = AcademicCalendarEvent.objects.filter(
            school=school, start_date__gte=timezone.localdate()
        ).order_by('start_date')[:5]

    stats = [
        {'label': 'Students', 'value': student_count, 'icon': 'fa-user-graduate',
         'tone': 'teal', 'hint': 'Active enrolment',
         'url': reverse('school_fees_student_list')},
        {'label': 'Classes', 'value': class_count, 'icon': 'fa-chalkboard',
         'tone': 'violet', 'hint': 'Active classes',
         'url': reverse('academics:class_subject_list')},
        {'label': 'Subjects', 'value': subject_count, 'icon': 'fa-book',
         'tone': '', 'hint': 'Active subjects',
         'url': reverse('academics:subject_list')},
        {'label': 'Teachers', 'value': teacher_count, 'icon': 'fa-chalkboard-teacher',
         'tone': 'slate', 'hint': 'Active teachers',
         'url': reverse('school_staff_list')},
    ]

    tasks = [
        {'title': 'Submissions awaiting review', 'count': pending_count,
         'icon': 'fa-paper-plane', 'tone': 'amber',
         'url': reverse('academics:submissions')},
        {'title': 'Ready to publish', 'count': ready_to_publish,
         'icon': 'fa-square-poll-vertical', 'tone': 'violet',
         'url': reverse('academics:results')},
        {'title': 'Submissions returned for correction', 'count': returned_count,
         'icon': 'fa-rotate-left', 'tone': 'rose',
         'url': reverse('academics:submissions')},
        {'title': 'Open requests & follow-ups', 'count': open_request_count,
         'icon': 'fa-list-check', 'tone': 'teal',
         'url': reverse('academics:update_request_list')},
        {'title': 'Overdue requests', 'count': overdue_request_count,
         'icon': 'fa-hourglass-half', 'tone': 'rose',
         'url': reverse('academics:update_request_list')},
        {'title': 'Classes with no attendance this term',
         'count': classes_without_attendance,
         'icon': 'fa-clipboard-check', 'tone': 'slate',
         'url': reverse('academics:attendance_report')},
    ]

    quick_actions = [
        {'label': 'Review submissions', 'icon': 'fa-paper-plane', 'tone': 'amber',
         'url': reverse('academics:submissions')},
        {'label': 'New request', 'icon': 'fa-list-check', 'tone': 'teal',
         'url': reverse('academics:update_request_create')},
        {'label': 'Publish results', 'icon': 'fa-square-poll-vertical', 'tone': 'violet',
         'url': reverse('academics:results')},
        {'label': 'Take attendance', 'icon': 'fa-clipboard-check', 'tone': '',
         'url': reverse('academics:attendance_index')},
    ]
    if can_override:
        quick_actions.append({
            'label': 'Academic-year override', 'icon': 'fa-clock-rotate-left',
            'tone': 'rose', 'url': reverse('academics:academic_year_override')})

    activity = []
    recent_subs = _scope(request, MarkSubmission.objects.select_related(
        'school_class', 'subject', 'submitted_by', 'reviewed_by')).order_by(
        '-updated_at')[:6]
    for sub in recent_subs:
        activity.append({
            'icon': 'fa-paper-plane',
            'tone': _submission_tone(sub.status),
            'title': f'{sub.subject.name} · {sub.school_class.name} — {sub.get_status_display()}',
            'meta': (sub.submitted_by.get_full_name() or sub.submitted_by.username)
                    if sub.submitted_by else '',
            'time': sub.updated_at,
            'url': reverse('academics:submission_review', args=[sub.pk]),
        })
    recent_sessions = _scope(request, AttendanceSession.objects.select_related(
        'school_class', 'subject', 'taken_by')).order_by('-created_at')[:4]
    for session in recent_sessions:
        activity.append({
            'icon': 'fa-clipboard-check',
            'tone': 'teal',
            'title': f'Attendance — {session.school_class.name}'
                     + (f' · {session.subject.name}' if session.subject_id else ''),
            'meta': session.date.isoformat(),
            'time': session.created_at,
            'url': reverse('academics:attendance_report') + f'?school_class={session.school_class_id}',
        })
    activity.sort(key=lambda item: item['time'], reverse=True)
    activity = activity[:8]

    attendance_chart = {
        'type': 'bar',
        'labels': [row['school_class'].name for row in attendance_rows][:12],
        'datasets': [{
            'label': 'Attendance %',
            'data': [float(row['percentage']) if row['percentage'] is not None else 0
                     for row in attendance_rows][:12],
            'backgroundColor': '#2563eb',
        }],
    }
    submission_chart = {
        'type': 'doughnut',
        'labels': ['Awaiting review', 'Approved', 'Returned'],
        'datasets': [{
            'data': [pending_count, approved_count, returned_count],
            'backgroundColor': ['#f59e0b', '#10b981', '#be123c'],
            'borderWidth': 0,
        }],
    }

    class_performance_chart = {
        'type': 'bar',
        'labels': [row['school_class'].name for row in class_performance][:12],
        'datasets': [{
            'label': 'Average mark %',
            'data': [float(row['average']) if row['average'] is not None else 0
                     for row in class_performance][:12],
            'backgroundColor': '#0d9488',
        }],
    }

    return render(request, 'academics/dashboard_head.html', {
        'school': school, 'year': year, 'term': term, 'years': years, 'terms': terms,
        'academic_context': academic_context,
        'active_override': active_override,
        'live_overrides': live_overrides,
        'can_override': can_override,
        'student_count': student_count,
        'class_count': class_count,
        'subject_count': subject_count,
        'teacher_count': teacher_count,
        'pending_submissions': pending_submissions,
        'pending_count': pending_count,
        'returned_count': returned_count,
        'approved_count': approved_count,
        'ready_to_publish': ready_to_publish,
        'open_request_count': open_request_count,
        'overdue_request_count': overdue_request_count,
        'recent_open_requests': recent_open_requests,
        'fee_summary': fee_summary,
        'class_performance': class_performance,
        'class_performance_chart': class_performance_chart,
        'attendance_rows': attendance_rows,
        'overall_attendance': overall_attendance,
        'publications': publications,
        'announcements': announcements,
        'events': events,
        'stats': stats,
        'tasks': tasks,
        'quick_actions': quick_actions,
        'activity': activity,
        'attendance_chart': attendance_chart,
        'submission_chart': submission_chart,
    })


# ---------------------------------------------------------------------------
# Exceptional academic-year override (authorized Head Teachers / Admins only)
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def academic_year_override(request):
    """Enable or disable an exceptional academic-year override.

    Only authorized Head Teachers / Admins can reach this view; teachers are
    redirected away before any query runs. Enabling requires a reason and is
    recorded in the audit log; disabling closes it and restores the school's
    configured active year.
    """
    if not academic_year_service.can_override(request.user):
        messages.error(
            request, 'You are not allowed to manage academic-year overrides.')
        return redirect('academics:home')

    ctx = _academic_context(request)
    school = ctx.school
    schools = []
    if school is None:
        accessible = scope_queryset(School.objects.filter(is_active=True), request)
        school_id = request.GET.get('school') or request.POST.get('school')
        school = accessible.filter(pk=school_id).first() if school_id else None
        if school is None:
            school = accessible.order_by('name').first()
        schools = list(accessible.order_by('name'))

    if school is None:
        messages.error(request, 'No school is available to configure an override.')
        return redirect('academics:head_dashboard')

    form = AcademicYearOverrideForm(
        request.POST or None, request=request, school=school)
    current = academic_year_service.active_override_for_school(school)

    if request.method == 'POST':
        action = (request.POST.get('action') or 'enable').strip()
        if action == 'disable':
            target = current
            override_id = request.POST.get('override')
            if override_id:
                target = get_object_or_404(AcademicYearOverride, pk=override_id)
            if target is None:
                messages.error(request, 'There is no active override to disable.')
            else:
                try:
                    academic_year_service.disable_override(
                        target, user=request.user,
                        reason=request.POST.get('disabled_reason', ''))
                except academic_year_service.AcademicYearError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        'Override disabled. Teachers return to the school’s '
                        'active academic year.')
                    return redirect(
                        f"{request.path}?school={school.pk}")
        elif form.is_valid():
            data = form.cleaned_data
            try:
                academic_year_service.enable_override(
                    user=request.user, school=school,
                    academic_year=data['academic_year'],
                    term=data.get('term'),
                    reason=data['reason'],
                    expires_at=data.get('expires_at'),
                    applies_to_all=data.get('applies_to_all', True),
                    teachers=data.get('teachers'))
            except academic_year_service.AcademicYearError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    'Academic-year override enabled. It is recorded in the '
                    'audit log and applies only within this school.')
                return redirect(f"{request.path}?school={school.pk}")

    history = list(AcademicYearOverride.objects.filter(school=school).select_related(
        'academic_year', 'term', 'enabled_by', 'disabled_by').order_by(
        '-enabled_at')[:10])
    return render(request, 'academics/academic_year_override.html', {
        'form': form,
        'school': school,
        'schools': schools,
        'override': current,
        'history': history,
        'active_context': _academic_context(request, school),
    })


# ---------------------------------------------------------------------------
# Requests & follow-ups
# ---------------------------------------------------------------------------
def _request_status_tone(status):
    return {
        UpdateRequest.Status.PENDING: 'neutral',
        UpdateRequest.Status.IN_PROGRESS: 'info',
        UpdateRequest.Status.SUBMITTED: 'warning',
        UpdateRequest.Status.COMPLETED: 'success',
        UpdateRequest.Status.CANCELLED: 'neutral',
    }.get(status, 'neutral')


@login_required
@role_required(*ACADEMIC_ROLES)
def update_request_list(request):
    """Every request the user may see (managers: school-wide; teachers: own)."""
    qs = _scope(request, UpdateRequest.objects.select_related(
        'assigned_to', 'school_class', 'subject', 'academic_year', 'term',
        'requested_by'))
    if access.is_teacher_only(request.user):
        qs = qs.filter(assigned_to=request.user)

    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(Q(title__icontains=q)
                       | Q(description__icontains=q)
                       | Q(assigned_to__first_name__icontains=q)
                       | Q(assigned_to__last_name__icontains=q)
                       | Q(school_class__name__icontains=q))
    status = _get_str(request, 'status')
    if status:
        if status == 'OPEN':
            qs = qs.filter(status__in=requests_service.OPEN_STATUSES)
        elif status and status in dict(UpdateRequest.Status.choices):
            qs = qs.filter(status=status)
    category = _get_str(request, 'category')
    if category and category in dict(UpdateRequest.Category.choices):
        qs = qs.filter(category=category)
    priority = _get_str(request, 'priority')
    if priority and priority in dict(UpdateRequest.Priority.choices):
        qs = qs.filter(priority=priority)
    overdue = _get_str(request, 'overdue')
    if overdue == '1':
        qs = qs.filter(status__in=requests_service.OPEN_STATUSES,
                       due_date__lt=timezone.localdate())

    open_count = qs.filter(status__in=requests_service.OPEN_STATUSES).count()
    base = qs
    submitted_count = qs.filter(status=UpdateRequest.Status.SUBMITTED).count()
    overdue_count = qs.filter(
        status__in=requests_service.OPEN_STATUSES,
        due_date__lt=timezone.localdate()).count()

    qs = qs.order_by('-created_at')
    page_obj = _paginate(request, qs, 20)
    for req in page_obj:
        req.status_tone = _request_status_tone(req.status)

    return render(request, 'academics/update_request_list.html', {
        'requests': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'open_count': open_count,
        'submitted_count': submitted_count,
        'overdue_count': overdue_count,
        'can_manage': access.can_review(request.user),
        'categories': UpdateRequest.Category.choices,
        'statuses': UpdateRequest.Status.choices,
        'priorities': UpdateRequest.Priority.choices,
        'filters': {'q': q, 'status': status, 'category': category,
                    'priority': priority, 'overdue': overdue},
    })


@login_required
@role_required(*REVIEW_ROLES)
def update_request_create(request):
    """Head-level users create a request assigned to a responsible teacher."""
    form = UpdateRequestForm(request.POST or None, request=request)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        school = data.get('school') or get_active_school(request)
        try:
            requests_service.create_update_request(
                request.user,
                school=school,
                title=data['title'],
                category=data['category'],
                description=data.get('description', ''),
                assigned_to=data['assigned_to'],
                priority=data['priority'],
                due_date=data.get('due_date'),
                academic_year=data.get('academic_year'),
                term=data.get('term'),
                school_class=data.get('school_class'),
                subject=data.get('subject'),
                student=data.get('student'),
                assessment=data.get('assessment'),
            )
        except requests_service.RequestError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Request created and assigned to '
                                      f'{data["assigned_to"].get_full_name() or data["assigned_to"].username}.')
            return redirect('academics:update_request_list')
    return render(request, 'academics/update_request_form.html', {'form': form})


@login_required
@role_required(*ACADEMIC_ROLES)
def update_request_detail(request, pk):
    """A single request: full event history, comments and status transitions."""
    request_obj = get_object_or_404(
        UpdateRequest.objects.select_related(
            'requested_by', 'assigned_to', 'school_class', 'subject',
            'student', 'assessment', 'academic_year', 'term'),
        pk=pk)
    request_obj.status_tone = _request_status_tone(request_obj.status)
    if not requests_service.can_view_request(request.user, request_obj):
        messages.error(request, 'You are not involved in this request.')
        return redirect('academics:update_request_list')

    events = request_obj.events.select_related('user').order_by('created_at')
    can_respond = requests_service.can_respond(request.user, request_obj)
    can_manage = requests_service.can_manage(request.user, request_obj)

    comment_form = UpdateRequestCommentForm(request.POST or None, prefix='comment')
    response_form = UpdateRequestResponseForm(request.POST or None, prefix='response')

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'comment':
            if not requests_service.can_view_request(request.user, request_obj):
                messages.error(request, 'You are not involved in this request.')
            elif comment_form.is_valid():
                try:
                    requests_service.add_comment(
                        request_obj, request.user, comment_form.cleaned_data['comment'])
                except requests_service.RequestError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, 'Comment added.')
                    return redirect('academics:update_request_detail', pk=request_obj.pk)

        elif action == 'transition':
            to_status = (request.POST.get('to_status') or '').strip().upper()
            # The response form is rendered with the ``response`` prefix, so its
            # textarea submits as ``response-notes``.
            notes = (request.POST.get('response-notes')
                     or request.POST.get('response_notes')
                     or request.POST.get('notes') or '').strip()
            if to_status == UpdateRequest.Status.SUBMITTED and not (notes or '').strip():
                messages.error(
                    request,
                    'Please write a response summarising what you completed.')
            elif not can_respond and not can_manage:
                messages.error(request, 'You are not allowed to change this request.')
            else:
                try:
                    requests_service.transition(
                        request_obj, request.user, to_status, notes=notes)
                except requests_service.RequestError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        f'Request moved to '
                        f'{dict(UpdateRequest.Status.choices)[to_status].lower()}.')
                    return redirect('academics:update_request_detail', pk=request_obj.pk)
        else:
            messages.error(request, 'Unrecognised action on this request.')

    return render(request, 'academics/update_request_detail.html', {
        'request_obj': request_obj,
        'events': events,
        'can_respond': can_respond,
        'can_manage': can_manage,
        'comment_form': comment_form,
        'response_form': response_form,
        'open_statuses': requests_service.OPEN_STATUSES,
    })


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def subject_list(request):
    qs = _scope(request, Subject.objects.all()).annotate(
        class_count=Count('class_subjects'))
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    status = _get_str(request, 'status')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    core = _get_str(request, 'core')
    if core == 'core':
        qs = qs.filter(is_core=True)
    elif core == 'elective':
        qs = qs.filter(is_core=False)
    sort = _get_str(request, 'sort') or 'name'
    qs = qs.order_by({'name': 'name', '-name': '-name', 'code': 'code',
                      '-code': '-code', 'classes': '-class_count'}.get(sort, 'name'))
    page_obj = _paginate(request, qs, 20)
    return render(request, 'academics/subject_list.html', {
        'subjects': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'filters': {'q': q, 'status': status, 'core': core, 'sort': sort},
    })


@login_required
@role_required(*REVIEW_ROLES)
def subject_form(request, pk=None):
    instance = get_object_or_404(Subject, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:subject_list')
    form = SubjectForm(request.POST or None, instance=instance, request=request)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        if _denied(request, obj):
            return redirect('academics:subject_list')
        messages.success(request, 'Subject saved.')
        return redirect('academics:subject_list')
    return render(request, 'academics/subject_form.html', {'form': form, 'instance': instance})


@login_required
@role_required(*REVIEW_ROLES)
def class_subject_list(request):
    qs = _scope(request, ClassSubject.objects.select_related('school_class', 'subject'))
    school_class_id = request.GET.get('school_class')
    if school_class_id:
        qs = qs.filter(school_class_id=school_class_id)
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(
            Q(subject__name__icontains=q) | Q(subject__code__icontains=q)
            | Q(school_class__name__icontains=q))
    status = _get_str(request, 'status')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    qs = qs.order_by('school_class__name', 'subject__name')
    page_obj = _paginate(request, qs, 20)
    return render(request, 'academics/class_subject_list.html', {
        'class_subjects': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'classes': _class_choices(request),
        'selected_class': school_class_id or '',
        'filters': {'q': q, 'status': status, 'school_class': school_class_id or ''},
    })


@login_required
@role_required(*REVIEW_ROLES)
def class_subject_form(request, pk=None):
    instance = get_object_or_404(ClassSubject, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:class_subject_list')
    form = ClassSubjectForm(request.POST or None, instance=instance, request=request)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, 'Class subject saved.')
        return redirect('academics:class_subject_list')
    return render(request, 'academics/class_subject_form.html',
                  {'form': form, 'instance': instance})


@login_required
@role_required(*REVIEW_ROLES)
def teacher_assignment_list(request):
    qs = _scope(request, TeacherAssignment.objects.select_related(
        'teacher', 'school_class', 'subject', 'academic_year', 'term'))
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(
            Q(teacher__first_name__icontains=q) | Q(teacher__last_name__icontains=q)
            | Q(teacher__username__icontains=q) | Q(school_class__name__icontains=q)
            | Q(subject__name__icontains=q))
    year_id = request.GET.get('academic_year')
    if year_id:
        qs = qs.filter(academic_year_id=year_id)
    term_id = request.GET.get('term')
    if term_id:
        qs = qs.filter(term_id=term_id)
    status = _get_str(request, 'status')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    qs = qs.order_by('teacher__first_name', 'teacher__last_name', 'school_class__name')
    page_obj = _paginate(request, qs, 25)
    years = scope_reference_queryset(AcademicYear.objects.all(), request).order_by('-name')
    terms = Term.objects.none()
    if year_id:
        terms = Term.objects.filter(academic_year_id=year_id).order_by('term_number')
    return render(request, 'academics/teacher_assignment_list.html', {
        'assignments': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'years': years,
        'terms': terms,
        'filters': {'q': q, 'academic_year': year_id or '', 'term': term_id or '',
                    'status': status},
    })


@login_required
@role_required(*REVIEW_ROLES)
def teacher_assignment_form(request, pk=None):
    instance = get_object_or_404(TeacherAssignment, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:teacher_assignment_list')
    form = TeacherAssignmentForm(request.POST or None, instance=instance, request=request)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, 'Teacher assignment saved.')
        return redirect('academics:teacher_assignment_list')
    return render(request, 'academics/teacher_assignment_form.html',
                  {'form': form, 'instance': instance})


@login_required
@role_required(*REVIEW_ROLES)
def grading_scale_list(request):
    scales = _scope(request, GradingScale.objects.all()).prefetch_related('bands')
    q = _get_str(request, 'q')
    if q:
        scales = scales.filter(name__icontains=q)
    status = _get_str(request, 'status')
    if status == 'active':
        scales = scales.filter(is_active=True)
    elif status == 'inactive':
        scales = scales.filter(is_active=False)
    scales = scales.order_by('-is_default', 'name')
    page_obj = _paginate(request, scales, 20)
    return render(request, 'academics/grading_scale_list.html', {
        'scales': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'filters': {'q': q, 'status': status},
    })


@login_required
@role_required(*REVIEW_ROLES)
def grading_scale_form(request, pk=None):
    instance = get_object_or_404(GradingScale, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:grading_scale_list')
    form = GradingScaleForm(request.POST or None, instance=instance, request=request)
    formset = GradeBandFormSet(request.POST or None, instance=instance or GradingScale())
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        obj = form.save()
        if obj.school_id is None:
            obj.school = get_active_school(request)
            obj.save(update_fields=['school'])
        formset.instance = obj
        formset.save()
        messages.success(request, 'Grading scale saved.')
        return redirect('academics:grading_scale_list')
    return render(request, 'academics/grading_scale_form.html',
                  {'form': form, 'formset': formset, 'instance': instance})


@login_required
@role_required(*REVIEW_ROLES)
def academic_settings(request):
    """Edit the academic settings for the active school (or a chosen one)."""
    school = get_active_school(request)
    if school is None:
        school_id = request.GET.get('school') or request.POST.get('school')
        school = _scope(request, School.objects.filter(is_active=True)).filter(
            pk=school_id).first()
        if school is None:
            school = _scope(request, School.objects.filter(is_active=True)).first()
    if school is None:
        messages.error(request, 'No school available to configure.')
        return redirect('academics:head_dashboard')

    instance = AcademicSettings.for_school(school)
    form = AcademicSettingsForm(
        request.POST or None, instance=instance, school=school)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Academic settings saved for {school}.')
        return redirect('academics:academic_settings')
    schools = _scope(request, School.objects.filter(is_active=True)) if get_active_school(request) is None else None
    return render(request, 'academics/academic_settings.html', {
        'form': form, 'school': school, 'schools': schools,
    })


@login_required
@role_required(*REVIEW_ROLES)
def report_fields(request):
    school = get_active_school(request)
    if school is None:
        school_id = request.GET.get('school') or request.POST.get('school')
        school = _scope(request, School.objects.filter(is_active=True)).filter(pk=school_id).first()
        if school is None:
            school = _scope(request, School.objects.filter(is_active=True)).first()
    if school is None:
        messages.error(request, 'No school available to configure.')
        return redirect('academics:head_dashboard')

    # Ensure a row exists for every key so administrators can configure them all.
    existing = {f.key for f in TerminalReportField.objects.filter(school=school)}
    for key, _label in TerminalReportField.FieldKey.choices:
        if key not in existing:
            TerminalReportField.objects.create(school=school, key=key, display_order=0)

    queryset = TerminalReportField.objects.filter(school=school).order_by('display_order', 'pk')
    formset = TerminalReportFieldFormSet(
        request.POST or None, instance=school, queryset=queryset)
    if request.method == 'POST' and formset.is_valid():
        formset.save()
        messages.success(request, f'Terminal report fields saved for {school}.')
        return redirect('academics:report_fields')
    schools = _scope(request, School.objects.filter(is_active=True)) if get_active_school(request) is None else None
    return render(request, 'academics/report_fields.html', {
        'formset': formset, 'school': school, 'schools': schools,
    })


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def assessment_list(request):
    qs = _scope(request, Assessment.objects.select_related(
        'school_class', 'subject', 'academic_year', 'term'))
    if access.is_teacher_only(request.user):
        pairs = access.teacher_class_subject_pairs(request.user)
        allowed = [a.pk for a in qs if a.subject_id in pairs.get(a.school_class_id, set())]
        qs = qs.filter(pk__in=allowed)
    school_class_id = request.GET.get('school_class')
    if school_class_id:
        qs = qs.filter(school_class_id=school_class_id)
    subject_id = request.GET.get('subject')
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    term_id = request.GET.get('term')
    if term_id:
        qs = qs.filter(term_id=term_id)
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(subject__name__icontains=q)
                       | Q(school_class__name__icontains=q))
    status = _get_str(request, 'status')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'archived':
        qs = qs.filter(is_active=False)
    qs = qs.order_by('-academic_year__name', 'term__term_number', 'name')
    page_obj = _paginate(request, qs, 25)
    return render(request, 'academics/assessment_list.html', {
        'assessments': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'classes': _class_choices(request),
        'subjects': _scope(request, Subject.objects.filter(is_active=True)).order_by('name'),
        'terms': scope_reference_queryset(
            Term.objects.all(), request, field='academic_year__school').select_related(
            'academic_year').order_by('-academic_year__name', 'term_number'),
        'selected_class': school_class_id or '',
        'filters': {'q': q, 'school_class': school_class_id or '',
                    'subject': subject_id or '', 'term': term_id or '', 'status': status},
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def assessment_form(request, pk=None):
    instance = get_object_or_404(Assessment, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:assessment_list')
    ctx = _academic_context(request)
    teacher = access.is_teacher_only(request.user)
    if access.is_teacher_only(request.user):
        # A teacher may only create/edit assessments in the active year, and
        # only when the school has a single active year and term configured.
        if ctx.year is None or ctx.term is None:
            messages.error(
                request,
                ctx.error_message or 'No active academic year and term are '
                'configured. Contact your Head Teacher or an Admin.')
            return redirect('academics:assessment_list')
        if instance is not None and instance.academic_year_id != ctx.year.pk:
            messages.error(
                request,
                'This assessment belongs to a previous academic year and is '
                'read-only.')
            return redirect('academics:assessment_list')

    form = AssessmentForm(
        request.POST or None, instance=instance, request=request, context=ctx)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if teacher and (obj.academic_year_id != ctx.year.pk
                        or obj.term_id != ctx.term.pk):
            messages.error(
                request,
                'Assessments must use the school’s active academic year and '
                'term.')
            return render(request, 'academics/assessment_form.html',
                          {'form': form, 'instance': instance,
                           'academic_context': ctx})
        if not access.can_edit_class_subject(
                request.user, obj.school_class, obj.subject,
                obj.academic_year, obj.term):
            messages.error(request, 'You are not assigned to that class/subject.')
            return render(request, 'academics/assessment_form.html',
                          {'form': form, 'instance': instance,
                           'academic_context': ctx})
        if obj.school_id is None:
            obj.school = obj.school_class.school
        obj.created_by = obj.created_by or request.user
        obj.save()
        messages.success(request, 'Assessment saved.')
        return redirect('academics:assessment_list')
    return render(request, 'academics/assessment_form.html', {
        'form': form, 'instance': instance, 'academic_context': ctx,
        'lock_year': teacher,
    })


@login_required
@role_required(*REVIEW_ROLES)
def assessment_delete(request, pk):
    assessment = get_object_or_404(Assessment, pk=pk)
    if _denied(request, assessment):
        return redirect('academics:assessment_list')
    if request.method == 'POST':
        assessment.is_active = False
        assessment.save(update_fields=['is_active'])
        messages.success(request, 'Assessment archived.')
    return redirect('academics:assessment_list')


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def attendance_index(request):
    year, term, academic_context = _active_year_term(request)
    lock_year = access.is_teacher_only(request.user)
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    terms = (Term.objects.filter(academic_year=year).order_by('term_number')
             if year is not None else Term.objects.none())
    classes = _class_choices(request)
    sessions = _scope(request, AttendanceSession.objects.select_related(
        'school_class', 'subject', 'taken_by'))
    if year is not None:
        sessions = sessions.filter(academic_year=year)
    if term is not None:
        sessions = sessions.filter(term=term)
    recent = sessions.order_by('-date', '-created_at')[:20]
    return render(request, 'academics/attendance_index.html', {
        'classes': classes, 'year': year, 'term': term, 'years': years,
        'terms': terms, 'recent': recent,
        'academic_context': academic_context, 'lock_year': lock_year,
        'class_count': classes.count(),
        'session_count': sessions.count(),
        'today': timezone.localdate(),
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def attendance_take(request):
    year, term, academic_context = _active_year_term(request)
    lock_year = access.is_teacher_only(request.user)
    class_id = request.GET.get('school_class') or request.POST.get('school_class')
    subject_id = request.GET.get('subject') or request.POST.get('subject') or None
    date_str = request.GET.get('date') or request.POST.get('date')
    session_label = request.GET.get('session_label') or request.POST.get('session_label') or 'Daily'

    school_class = _class_choices(request).filter(pk=class_id).first()
    if school_class is None or year is None or term is None:
        if lock_year and academic_context.error_message:
            messages.error(request, academic_context.error_message)
        else:
            messages.error(request, 'Choose a class, academic year and term first.')
        return redirect('academics:attendance_index')

    subject = None
    if subject_id:
        subject = _scope(request, Subject.objects.all()).filter(pk=subject_id).first()
    if not access.can_take_attendance(request.user, school_class, subject, year, term):
        messages.error(request, 'You are not allowed to take attendance for that class.')
        return redirect('academics:attendance_index')

    date = timezone.localdate()
    if date_str:
        try:
            date = timezone.datetime.fromisoformat(date_str).date()
        except ValueError:
            date = timezone.localdate()

    session, _created = attendance_service.get_or_create_session(
        school_class=school_class, subject=subject, academic_year=year, term=term,
        date=date, session_label=session_label, user=request.user)

    students = school_class.students.filter(is_active=True).order_by('last_name', 'first_name')

    if request.method == 'POST':
        entries = []
        for student in students:
            status = request.POST.get(f'status_{student.pk}', AttendanceRecord.Status.PRESENT)
            note = request.POST.get(f'note_{student.pk}', '')
            entries.append((student, status, note))
        attendance_service.save_attendance(
            session, entries, user=request.user,
            reason=request.POST.get('reason', ''))
        messages.success(request, 'Attendance saved.')
        return redirect(f"{request.path}?school_class={school_class.pk}"
                        f"&date={date.isoformat()}&session_label={session_label}"
                        + (f"&subject={subject.pk}" if subject else '')
                        + f"&academic_year={year.pk}&term={term.pk}")

    existing = {r.student_id: r for r in session.records.all()}
    rows = [{'student': s, 'record': existing.get(s.pk)} for s in students]
    subjects = access.visible_class_subjects(request, school_class, year, term)
    return render(request, 'academics/attendance_take.html', {
        'session': session, 'school_class': school_class, 'subject': subject,
        'date': date, 'session_label': session_label, 'rows': rows,
        'year': year, 'term': term, 'subjects': subjects,
        'academic_context': academic_context, 'lock_year': lock_year,
        'status_choices': AttendanceRecord.Status.choices,
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def attendance_report(request):
    year, term, academic_context = _active_year_term(request)
    lock_year = access.is_teacher_only(request.user)
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    terms = (Term.objects.filter(academic_year=year).order_by('term_number')
             if year is not None else Term.objects.none())
    class_id = request.GET.get('school_class')
    school_class = _class_choices(request).filter(pk=class_id).first()
    rows = []
    if school_class is not None and year is not None and term is not None:
        rows = attendance_service.class_summary(school_class, year, term)
    return render(request, 'academics/attendance_report.html', {
        'classes': _class_choices(request), 'school_class': school_class,
        'year': year, 'term': term, 'years': years, 'terms': terms, 'rows': rows,
        'academic_context': academic_context, 'lock_year': lock_year,
    })


# ---------------------------------------------------------------------------
# Marks entry / submission / review
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def mark_entry(request, pk):
    assessment = get_object_or_404(
        Assessment.objects.select_related('school_class', 'subject', 'academic_year', 'term'),
        pk=pk)
    if _denied(request, assessment):
        return redirect('academics:assessment_list')
    academic_context = _academic_context(request)
    if access.is_teacher_only(request.user):
        # Server-side guard: a teacher may never enter marks for a year other
        # than the resolved active one, even by opening a historical URL.
        if academic_context.year is None:
            messages.error(
                request,
                academic_context.error_message
                or 'No active academic year is configured. Contact your Head '
                'Teacher or an Admin.')
            return redirect('academics:teacher_dashboard')
        if assessment.academic_year_id != academic_context.year.pk:
            messages.error(
                request,
                'This assessment belongs to a previous academic year and is '
                'read-only.')
            return redirect('academics:assessment_list')
    if not access.can_edit_class_subject(
            request.user, assessment.school_class, assessment.subject,
            assessment.academic_year, assessment.term):
        messages.error(request, 'You are not assigned to that class/subject.')
        return redirect('academics:teacher_dashboard')

    locked = ResultPublication.is_locked(
        assessment.academic_year, assessment.term, assessment.school_class)

    students = list(assessment.school_class.students.filter(
        is_active=True).order_by('last_name', 'first_name'))

    existing = {m.student_id: m for m in assessment.marks.all()}
    # When a POST fails validation we keep whatever the user typed so no valid
    # marks are lost. ``None`` means "fall back to the stored value".
    posted = None

    if request.method == 'POST':
        if locked:
            messages.error(request, 'Results are published and locked.')
            return redirect(request.path)
        entries = []
        for student in students:
            score = request.POST.get(f'score_{student.pk}')
            status = request.POST.get(f'status_{student.pk}', 'PRESENT')
            entries.append((student, score, status))
        try:
            marks.save_marks(assessment, entries, request.user)
        except marks.MarkingError as exc:
            messages.error(request, str(exc))
            # Re-render with the submitted values rather than discarding them.
            posted = {
                s.pk: (request.POST.get(f'score_{s.pk}', ''),
                       request.POST.get(f'status_{s.pk}', 'PRESENT'))
                for s in students
            }
        else:
            messages.success(request, 'Marks saved.')
            if request.POST.get('submit'):
                try:
                    marks.submit_for_review(
                        assessment.academic_year, assessment.term,
                        assessment.school_class, assessment.subject, request.user)
                    messages.success(request, 'Marks submitted for review.')
                except marks.MarkingError as exc:
                    messages.error(request, str(exc))
            return redirect('academics:mark_entry', pk=assessment.pk)

    rows = []
    entered = 0
    for student in students:
        mark = existing.get(student.pk)
        if posted is not None:
            score_value, status_value = posted[student.pk]
        elif mark is not None:
            score_value = '' if mark.score is None else str(mark.score.normalize())
            status_value = mark.status
        else:
            score_value, status_value = '', 'PRESENT'
        if str(score_value).strip() != '':
            entered += 1
        rows.append({
            'student': student, 'mark': mark,
            'score': score_value, 'status': status_value,
        })

    submission = marks.submission_for(
        assessment.academic_year, assessment.term,
        assessment.school_class, assessment.subject, create=False)
    return render(request, 'academics/mark_entry.html', {
        'assessment': assessment, 'rows': rows, 'locked': locked,
        'submission': submission, 'academic_context': academic_context,
        'entered_count': entered,
        'total_count': len(students),
        'status_choices': [('PRESENT', 'Present'), ('ABSENT', 'Absent'),
                           ('EXEMPT', 'Exempt'), ('MISSING', 'Not entered')],
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def submissions(request):
    qs = _scope(request, MarkSubmission.objects.select_related(
        'school_class', 'subject', 'submitted_by', 'reviewed_by',
        'academic_year', 'term'))
    if access.is_teacher_only(request.user):
        qs = qs.filter(submitted_by=request.user)
    else:
        qs = qs.exclude(status=MarkSubmission.Status.DRAFT)
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(
            Q(school_class__name__icontains=q) | Q(subject__name__icontains=q)
            | Q(submitted_by__first_name__icontains=q)
            | Q(submitted_by__last_name__icontains=q)
            | Q(submitted_by__username__icontains=q))
    status = _get_str(request, 'status')
    if status:
        qs = qs.filter(status=status)
    school_class_id = request.GET.get('school_class')
    if school_class_id:
        qs = qs.filter(school_class_id=school_class_id)
    subject_id = request.GET.get('subject')
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    qs = qs.order_by('-updated_at')
    for submission in qs:
        submission.tone = _submission_tone(submission.status)
    page_obj = _paginate(request, qs, 20)
    return render(request, 'academics/submissions.html', {
        'submissions': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'can_review': access.can_review(request.user),
        'classes': _class_choices(request),
        'subjects': _scope(request, Subject.objects.filter(is_active=True)).order_by('name'),
        'statuses': MarkSubmission.Status.choices,
        'filters': {'q': q, 'status': status, 'school_class': school_class_id or '',
                    'subject': subject_id or ''},
    })


@login_required
@role_required(*REVIEW_ROLES)
def submission_review(request, pk):
    """Review a submitted set of marks.

    GET shows the submission with a marks preview and, while it is awaiting
    review, an action panel. POST performs the review transition:

    * ``approve`` moves the submission to ``APPROVED`` so results can be
      published downstream;
    * ``return`` moves it to ``RETURNED`` and requires reviewer feedback.

    The workflow has no separate "rejected" state, so returning for corrections
    is also how a reviewer rejects a submission that must not be approved.
    """
    submission = get_object_or_404(
        MarkSubmission.objects.select_related(
            'school_class', 'subject', 'term', 'academic_year',
            'submitted_by', 'reviewed_by'),
        pk=pk)
    if _denied(request, submission):
        return redirect('academics:submissions')

    assessments = list(Assessment.objects.filter(
        academic_year=submission.academic_year, term=submission.term,
        school_class=submission.school_class, subject=submission.subject,
        is_active=True).order_by('created_at'))
    students = list(submission.school_class.students.filter(
        is_active=True).order_by('last_name', 'first_name'))

    scale = resolve_scale(submission.school_class.school)

    # One preview row per student: the student, their marks for each assessment
    # in this submission, and the weighted subject result.
    marks_by_key = {
        (mark.student_id, mark.assessment_id): mark
        for mark in StudentMark.objects.filter(
            assessment__in=assessments, student__in=students)
    }
    preview = []
    for student in students:
        result = grading_subject_result(
            student, submission.academic_year, submission.term,
            submission.subject, scale=scale)
        result['student'] = student
        cells = [
            {'assessment': assessment,
             'mark': marks_by_key.get((student.pk, assessment.pk))}
            for assessment in assessments
        ]
        preview.append({'student': student, 'result': result, 'cells': cells})

    awaiting_review = submission.status == MarkSubmission.Status.SUBMITTED

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        comment = (request.POST.get('comment') or '').strip()

        # Controlled correction: reopen an APPROVED submission with a recorded
        # reason so the teacher may edit and resubmit the marks.
        if action == 'reopen':
            if submission.status != MarkSubmission.Status.APPROVED:
                messages.error(
                    request,
                    'Only approved submissions can be reopened for correction.')
            else:
                reason = (request.POST.get('reason') or '').strip()
                if not reason:
                    messages.error(
                        request,
                        'A reason is required to reopen approved results for '
                        'correction.')
                else:
                    try:
                        marks.open_mark_correction(
                            submission.academic_year, submission.term,
                            submission.school_class, submission=submission,
                            user=request.user, reason=reason)
                    except marks.MarkingError as exc:
                        messages.error(request, str(exc))
                    else:
                        messages.success(
                            request,
                            'Submission reopened for correction. The teacher can '
                            'now edit and resubmit the marks.')
                        return redirect('academics:submissions')
            return redirect('academics:submission_review', pk=submission.pk)

        if not awaiting_review:
            messages.error(
                request,
                'This submission is no longer awaiting review '
                f'(current status: {submission.get_status_display()}).')
            return redirect('academics:submission_review', pk=submission.pk)

        # A teacher must never approve their own submission; a reviewer cannot
        # sign off their own entry either, unless they are a superuser.
        if (submission.submitted_by_id == request.user.pk
                and not request.user.is_superuser):
            messages.error(
                request,
                'You cannot review a submission you submitted yourself.')
            return redirect('academics:submission_review', pk=submission.pk)

        if action == 'approve':
            try:
                marks.review_submission(submission, True, request.user, comment)
            except marks.MarkingError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    'Submission approved. The results are now available for '
                    'publication.')
                return redirect('academics:submissions')
        elif action == 'return':
            if not comment:
                messages.error(
                    request,
                    'Please provide feedback explaining what the teacher must '
                    'correct before returning the submission.')
            else:
                try:
                    marks.review_submission(
                        submission, False, request.user, comment)
                except marks.MarkingError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        'Submission returned to the teacher for correction.')
                    return redirect('academics:submissions')
        else:
            messages.error(request, 'Unrecognised review action.')

    return render(request, 'academics/submission_review.html', {
        'submission': submission,
        'assessments': assessments,
        'preview': preview,
        'students': students,
        'can_review': access.can_review(request.user),
        'awaiting_review': awaiting_review,
        'can_reopen': (awaiting_review is False
                       and submission.status == MarkSubmission.Status.APPROVED),
    })


# ---------------------------------------------------------------------------
# Results, publication and promotion
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def results(request):
    year, term, academic_context = _active_year_term(request)
    lock_year = access.is_teacher_only(request.user)
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    terms = (Term.objects.filter(academic_year=year).order_by('term_number')
             if year is not None else Term.objects.none())
    school_class = _class_choices(request).filter(
        pk=request.GET.get('school_class')).first()
    rows, subjects, analytics = [], [], None
    publication = None
    if school_class is not None and year is not None and term is not None:
        rows, subjects = results_service.class_results(school_class, year, term)
        analytics = results_service.class_analytics(rows, subjects)
        publication = results_service.publication_state(year, term, school_class)

    if request.method == 'POST' and school_class is not None and year and term:
        if not access.can_review(request.user):
            messages.error(request, 'You are not allowed to publish results.')
        else:
            action = request.POST.get('action')
            if action == 'publish':
                marks.publish_results(year, term, school_class, request.user)
                messages.success(request, 'Results published.')
            elif action == 'unpublish':
                reason = request.POST.get('reason', '')
                try:
                    marks.unpublish_results(
                        year, term, school_class, request.user, reason=reason)
                except marks.MarkingError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(
                        request,
                        'Results unpublished. The reason is recorded in the audit '
                        'log. Reopen the affected submissions for correction '
                        'before editing their marks.')
        return redirect(f"{request.path}?school_class={school_class.pk}"
                        f"&academic_year={year.pk}&term={term.pk}")

    grade_chart = None
    subject_chart = None
    if analytics:
        subject_chart = {
            'type': 'bar',
            'labels': [row['subject'].name for row in analytics['subjects']],
            'datasets': [{
                'label': 'Subject average %',
                'data': [float(row['average']) if row['average'] is not None else 0
                         for row in analytics['subjects']],
                'backgroundColor': '#0d9488',
            }],
        }
    grade_counts = Counter()
    for row in rows:
        for result in row['subjects']:
            if result['grade']:
                grade_counts[result['grade']] += 1
    if grade_counts:
        grade_chart = {
            'type': 'doughnut',
            'labels': list(grade_counts.keys()),
            'datasets': [{
                'data': list(grade_counts.values()),
                'backgroundColor': ['#2563eb', '#0d9488', '#6d28d9', '#f59e0b',
                                    '#be123c', '#0ea5e9'],
                'borderWidth': 0,
            }],
        }

    return render(request, 'academics/results.html', {
        'classes': _class_choices(request), 'school_class': school_class,
        'year': year, 'term': term, 'years': years, 'terms': terms,
        'academic_context': academic_context, 'lock_year': lock_year,
        'rows': rows, 'subjects': subjects, 'analytics': analytics,
        'publication': publication, 'can_review': access.can_review(request.user),
        'subject_chart': subject_chart, 'grade_chart': grade_chart,
    })


@login_required
@role_required(*REVIEW_ROLES)
def promote(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    if _denied(request, student):
        return redirect('academics:results')
    year, term, years, terms = _select_year_term(request)
    if year is None:
        messages.error(request, 'No academic year available.')
        return redirect('academics:results')

    comment = TerminalComment.objects.filter(
        student=student, academic_year=year, term=term).first()
    comment_form = TerminalCommentForm(
        request.POST or None, instance=comment,
        prefix='comment')
    promotion_form = PromotionForm(
        request.POST or None, school=student.school,
        current_class=student.school_class, prefix='promotion')

    if request.method == 'POST':
        if 'save_comment' in request.POST and comment_form.is_valid():
            obj = comment_form.save(commit=False)
            obj.student = student
            obj.academic_year = year
            obj.term = term
            obj.school = student.school
            obj.updated_by = request.user
            obj.save()
            messages.success(request, 'Report remarks saved.')
            return redirect('academics:promote', student_id=student.pk)
        if 'save_promotion' in request.POST and promotion_form.is_valid():
            data = promotion_form.cleaned_data
            from .services import promotion as promotion_service
            promotion_service.promote_student(
                student, year, data['decision'], data.get('to_class'),
                user=request.user, notes=data.get('notes', ''))
            messages.success(request, 'Promotion recorded.')
            return redirect('academics:results')

    return render(request, 'academics/promote.html', {
        'student': student, 'year': year, 'term': term,
        'comment_form': comment_form, 'promotion_form': promotion_form,
        'history': PromotionRecord.objects.filter(student=student).select_related(
            'academic_year', 'from_class', 'to_class'),
    })


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------
def _get_report_student(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    if _denied(request, student):
        return None
    return student


@login_required
@role_required(*ACADEMIC_ROLES)
def terminal_report(request, student_id):
    student = get_object_or_404(Student, pk=student_id)
    if _denied(request, student):
        return redirect('academics:head_dashboard')
    year, term, academic_context = _active_year_term(request)
    lock_year = access.is_teacher_only(request.user)
    years = scope_reference_queryset(
        AcademicYear.objects.all(), request).order_by('-start_date', '-name')
    terms = (Term.objects.filter(academic_year=year).order_by('term_number')
             if year is not None else Term.objects.none())
    if year is None or term is None:
        messages.error(request, 'Select an academic year and term.')
        return redirect('academics:head_dashboard')
    data = reports.terminal_report_data(student, year, term)
    return render(request, 'academics/terminal_report.html', {
        'data': data, 'student': student, 'year': year, 'term': term,
        'years': years, 'terms': terms,
        'academic_context': academic_context, 'lock_year': lock_year,
    })


@login_required
@role_required(*ACADEMIC_ROLES)
def terminal_report_pdf(request):
    student = get_object_or_404(Student, pk=request.GET.get('student'))
    if _denied(request, student):
        return redirect('academics:head_dashboard')
    year, term, _academic_context = _active_year_term(request)
    if year is None or term is None:
        messages.error(request, 'Select an academic year and term.')
        return redirect('academics:head_dashboard')
    data = reports.terminal_report_data(student, year, term)
    pdf_bytes = _render_terminal_pdf(data)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="terminal_report_{student.student_id}.pdf"')
    return response


def _render_terminal_pdf(data):
    """Render a terminal report to PDF with reportlab (best effort)."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = []
    school = data['school']
    student = data['student']
    story.append(Paragraph(school.name if school else 'Terminal Report', styles['Title']))
    story.append(Paragraph(
        f"{data['academic_year'].name} - {data['term'].name}", styles['Heading3']))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f"Student: {student.get_full_name()} ({student.student_id})", styles['Normal']))
    story.append(Paragraph(
        f"Class: {student.school_class.name}", styles['Normal']))
    story.append(Spacer(1, 4 * mm))

    rows = [['Subject', 'Score %', 'Grade', 'Remark']]
    for result in data['results']['subjects']:
        rows.append([
            str(result['subject']),
            f"{result['percentage']}" if result['percentage'] is not None else '—',
            result['grade'] or '—',
            (result['remark'] or '')[:40],
        ])
    table = Table(rows, colWidths=[70 * mm, 30 * mm, 25 * mm, 50 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))

    average = data['results']['average']
    story.append(Paragraph(
        f"Average: {average if average is not None else '—'}   "
        f"Attendance: {data['attendance']['percentage'] or '—'}%", styles['Normal']))
    if data['comment']:
        if data['comment'].class_teacher_comment:
            story.append(Paragraph(
                f"Class teacher: {data['comment'].class_teacher_comment}", styles['Normal']))
        if data['comment'].head_teacher_remark:
            story.append(Paragraph(
                f"Head teacher: {data['comment'].head_teacher_remark}", styles['Normal']))
    story.append(Spacer(1, 4 * mm))

    if data['report_fields']:
        fin_rows = [['Fees', 'Amount']]
        for field in data['report_fields']:
            value = field['formatted'] if field['value'] is not None else (field['flag'] or '—')
            fin_rows.append([field['label'], str(value)])
        fin_table = Table(fin_rows, colWidths=[80 * mm, 45 * mm])
        fin_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6c757d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        story.append(fin_table)

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Calendar & announcements
# ---------------------------------------------------------------------------
@login_required
@role_required(*ACADEMIC_ROLES)
def calendar(request):
    qs = _scope(request, AcademicCalendarEvent.objects.all()).order_by('start_date')
    type_filter = _get_str(request, 'type')
    if type_filter:
        qs = qs.filter(event_type=type_filter)
    events = list(qs)
    can_manage = access.can_review(request.user)
    calendar_events = [{
        'id': event.pk,
        'title': event.title,
        'start': event.start_date.isoformat(),
        'end': (event.end_date or event.start_date).isoformat(),
        'type': event.event_type,
        'typeLabel': event.get_event_type_display(),
        'description': event.description or '',
        'school': event.school.display_name if event.school_id else '',
        'editUrl': reverse('academics:calendar_edit', args=[event.pk]) if can_manage else '',
    } for event in events]
    return render(request, 'academics/calendar.html', {
        'events': events,
        'event_types': AcademicCalendarEvent.EventType.choices,
        'selected_type': type_filter,
        'can_manage': can_manage,
        'calendar_data': {
            'events': calendar_events,
            'canManage': can_manage,
            'today': timezone.localdate().isoformat(),
            'createUrl': reverse('academics:calendar_create') if can_manage else '',
        },
    })


@login_required
@role_required(*REVIEW_ROLES)
def calendar_event_form(request, pk=None):
    instance = get_object_or_404(AcademicCalendarEvent, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:calendar')
    form = AcademicCalendarEventForm(request.POST or None, instance=instance, request=request)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if obj.school_id is None:
            obj.school = get_active_school(request)
        obj.created_by = obj.created_by or request.user
        obj.save()
        messages.success(request, 'Calendar event saved.')
        return redirect('academics:calendar')
    return render(request, 'academics/calendar_form.html', {'form': form, 'instance': instance})


@login_required
@role_required(*REVIEW_ROLES)
def calendar_event_delete(request, pk):
    event = get_object_or_404(AcademicCalendarEvent, pk=pk)
    if _denied(request, event):
        return redirect('academics:calendar')
    if request.method == 'POST':
        event.delete()
        messages.success(request, 'Calendar event deleted.')
    return redirect('academics:calendar')


@login_required
@role_required(*ACADEMIC_ROLES)
def announcements(request):
    qs = _scope(request, Announcement.objects.all()).order_by('-published_at')
    if access.is_teacher_only(request.user):
        qs = qs.filter(is_published=True, audience__in=['ALL', 'TEACHERS'])
    q = _get_str(request, 'q')
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    audience = _get_str(request, 'audience')
    if audience:
        qs = qs.filter(audience=audience)
    status = _get_str(request, 'status')
    if status == 'published':
        qs = qs.filter(is_published=True)
    elif status == 'draft':
        qs = qs.filter(is_published=False)
    page_obj = _paginate(request, qs, 20)
    return render(request, 'academics/announcements.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'total_count': page_obj.paginator.count,
        'can_manage': access.can_review(request.user),
        'audiences': Announcement.Audience.choices,
        'filters': {'q': q, 'audience': audience, 'status': status},
    })


@login_required
@role_required(*REVIEW_ROLES)
def announcement_form(request, pk=None):
    instance = get_object_or_404(Announcement, pk=pk) if pk else None
    if instance and _denied(request, instance):
        return redirect('academics:announcements')
    form = AnnouncementForm(request.POST or None, instance=instance, request=request)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if obj.school_id is None:
            obj.school = get_active_school(request)
        obj.created_by = obj.created_by or request.user
        obj.save()
        messages.success(request, 'Announcement saved.')
        return redirect('academics:announcements')
    return render(request, 'academics/announcement_form.html',
                  {'form': form, 'instance': instance})
