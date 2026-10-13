"""Active academic-year enforcement and exceptional overrides.

These tests pin the server-side contract introduced for the academics module:

* teachers always work in their school's single active academic year;
* a forged year (in a GET/POST/URL) can never change that;
* a school with no active year - or several - blocks new academic records and
  surfaces the configuration problem instead of silently picking a year;
* only authorized Head Teachers/Admins can open a reason-logged, audited,
  time-boxed override, and closing it restores the active year;
* each school resolves its own active year independently.

The fixtures deliberately mirror ``tests/test_academics.py`` so the flows are
established on the same scaffold used by the rest of the module's tests.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import (
    AcademicYearOverride,
    Assessment,
    AttendanceSession,
    ClassSubject,
    Subject,
    TeacherAssignment,
)
from apps.academics.services import academic_year as ay
from apps.audit.models import AuditLog
from apps.school_fees.models import AcademicYear, SchoolClass, Term
from apps.schools.models import School

from tests.test_academics import build_assessment, build_school

User = get_user_model()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def school_a(db):
    school, _ = School.objects.get_or_create(
        code='MANKARIGU', defaults={'name': 'Mankarigu', 'short_name': 'Mankarigu'})
    return school


@pytest.fixture
def school_b(db):
    school, _ = School.objects.get_or_create(
        code='LINGBINSI', defaults={'name': 'Lingbinsi', 'short_name': 'Lingbinsi'})
    return school


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username='ay_admin', email='ay_admin@test.com', password='x',
        first_name='Ada', last_name='Admin', role='SUPER_ADMIN', is_staff=True)


@pytest.fixture
def head_a(db, school_a):
    return User.objects.create_user(
        username='head_a_ay', email='ha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='teacher_a_ay', email='ta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


@pytest.fixture
def teacher_b(db, school_b):
    return User.objects.create_user(
        username='teacher_b', email='tb@test.com', password='x',
        first_name='Tee', last_name='B', role='TEACHER', school=school_b)


def extra_year(school, name='2024/2025', active=False, term_name='First Term'):
    """A second academic year (and term) inside ``school``."""
    year = AcademicYear.objects.create(school=school, name=name, is_active=active)
    term = Term.objects.create(academic_year=year, name=term_name, term_number=1)
    return year, term


def assign(teacher, data, term=None):
    return TeacherAssignment.objects.create(
        teacher=teacher, school_class=data.school_class, subject=data.subject,
        academic_year=data.year, term=term)


def context_for(user, path='/'):
    return ay.build_context(request_for(user))


def request_for(user, path='/'):
    request = RequestFactory().get(path)
    request.user = user
    request.session = {}
    return request


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestResolution:
    def test_teacher_resolves_school_active_year(self, school_a, teacher_a):
        data = build_school(school_a, 'A1')
        ctx = context_for(teacher_a)
        assert ctx.status == ay.STATUS_OK
        assert ctx.year == data.year
        assert ctx.term == data.term

    def test_each_school_resolves_its_own_active_year(
            self, school_a, school_b, teacher_a, teacher_b):
        a = build_school(school_a, 'A2')
        b = build_school(school_b, 'A3')
        assert context_for(teacher_a).year == a.year
        assert context_for(teacher_b).year == b.year
        assert a.year != b.year

    def test_combined_view_resolves_each_school_separately(self, school_a, school_b, admin_user):
        a = build_school(school_a, 'A4')
        b = build_school(school_b, 'A5')
        ctx = context_for(admin_user)
        assert ctx.status == ay.STATUS_COMBINED
        assert ctx.year is None
        assert ctx.per_school[a.school.pk]['year'] == a.year
        assert ctx.per_school[b.school.pk]['year'] == b.year

    def test_no_active_year_is_flagged(self, school_a, teacher_a):
        # build_school creates the only year; deactivate it.
        data = build_school(school_a, 'A6')
        AcademicYear.objects.filter(pk=data.year.pk).update(is_active=False)
        ctx = context_for(teacher_a)
        assert ctx.status == ay.STATUS_NONE
        assert ctx.year is None
        assert 'active' in ctx.error_message.lower()

    def test_multiple_active_years_flagged(self, school_a, teacher_a):
        data = build_school(school_a, 'A7')
        extra_year(school_a, '2024/2025', active=True)
        ctx = context_for(teacher_a)
        assert ctx.status == ay.STATUS_MULTIPLE
        assert ctx.year is None

    def test_assert_year_allowed_guards_teachers(self, school_a, teacher_a):
        data = build_school(school_a, 'A7b')
        year_b, _term_b = extra_year(school_a, '2011/2012')
        request = request_for(teacher_a)
        assert ay.assert_year_allowed(request, data.year) is True
        assert ay.assert_year_allowed(request, year_b) is False

    def test_enforce_active_year_raises_without_configuration(self, school_a, teacher_a):
        data = build_school(school_a, 'A7c')
        AcademicYear.objects.filter(pk=data.year.pk).update(is_active=False)
        with pytest.raises(ay.AcademicYearError):
            ay.enforce_active_year(request_for(teacher_a))

    def test_override_switches_resolved_year(self, school_a, teacher_a, head_a):
        data = build_school(school_a, 'A8')
        year_b, term_b = extra_year(school_a, '2023/2024')
        ay.enable_override(user=head_a, school=school_a, academic_year=year_b,
                           term=term_b, reason='Backfill')
        ctx = context_for(teacher_a)
        assert ctx.year == year_b
        assert ctx.term == term_b
        assert ctx.is_override is True
        # The configured active year is untouched.
        data.year.refresh_from_db()
        assert data.year.is_active is True

    def test_override_scope_limits_teachers(self, school_a, teacher_a, head_a):
        build_school(school_a, 'A9')
        year_b, term_b = extra_year(school_a, '2022/2023')
        other = User.objects.create_user(
            username='other_teacher', email='o@test.com', password='x',
            role='TEACHER', school=school_a)
        ay.enable_override(user=head_a, school=school_a, academic_year=year_b,
                           term=term_b, reason='Targeted', applies_to_all=False,
                           teachers=[teacher_a])
        assert context_for(teacher_a).year == year_b
        assert context_for(other).year != year_b


# --------------------------------------------------------------------------- #
# Teachers cannot choose another year
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestTeacherForcedYear:
    def _assessment_payload(self, data, year, term, name='Test'):
        return {
            'academic_year': year.pk, 'term': term.pk,
            'school_class': data.school_class.pk, 'subject': data.subject.pk,
            'name': name, 'assessment_type': 'EXAM', 'max_score': '100',
            'weight': '100', 'date': '', 'marks_due_date': '', 'is_active': 'on',
        }

    def test_teacher_create_ignores_forged_year(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F1')
        year_b, term_b = extra_year(school_a, '2019/2020')
        assign(teacher_a, data)
        client.force_login(teacher_a)
        resp = client.post(
            reverse('academics:assessment_create'),
            self._assessment_payload(data, year_b, term_b, name='Forged'))
        assert resp.status_code == 302
        created = Assessment.objects.get(name='Forged')
        assert created.academic_year_id == data.year.pk
        assert created.term_id == data.term.pk

    def test_teacher_dashboard_reports_active_year(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F2')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:teacher_dashboard'))
        assert resp.status_code == 200
        assert resp.context['year'] == data.year

    def test_teacher_cannot_open_mark_entry_for_other_year(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F3')
        year_b, term_b = extra_year(school_a, '2018/2019')
        assign(teacher_a, data)
        other = Assessment.objects.create(
            academic_year=year_b, term=term_b, school_class=data.school_class,
            subject=data.subject, name='Old', max_score=100, weight=100)
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:mark_entry', args=[other.pk]))
        assert resp.status_code == 302

    def test_teacher_attendance_uses_active_year(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F4', student_count=1)
        year_b, term_b = extra_year(school_a, '2017/2018')
        assign(teacher_a, data)
        client.force_login(teacher_a)
        resp = client.post(reverse('academics:attendance_take'), {
            'school_class': data.school_class.pk,
            'academic_year': year_b.pk, 'term': term_b.pk,
            'date': '2026-03-01', 'session_label': 'Daily',
            f'status_{data.students[0].pk}': 'PRESENT',
        })
        assert resp.status_code == 302
        session = AttendanceSession.objects.get(date='2026-03-01')
        assert session.academic_year_id == data.year.pk
        assert session.term_id == data.term.pk

    def test_no_active_year_blocks_creation(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F5')
        assign(teacher_a, data)
        AcademicYear.objects.filter(pk=data.year.pk).update(is_active=False)
        client.force_login(teacher_a)
        resp = client.post(
            reverse('academics:assessment_create'),
            self._assessment_payload(data, data.year, data.term))
        assert resp.status_code == 302
        assert not Assessment.objects.filter(name='Test').exists()

    def test_multiple_active_years_blocks_creation(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F6')
        assign(teacher_a, data)
        extra_year(school_a, '2016/2017', active=True)
        client.force_login(teacher_a)
        resp = client.post(
            reverse('academics:assessment_create'),
            self._assessment_payload(data, data.year, data.term))
        assert resp.status_code == 302
        assert not Assessment.objects.filter(name='Test').exists()

    def test_historical_records_are_not_moved(self, client, teacher_a, school_a):
        data = build_school(school_a, 'F7')
        assessment = build_assessment(data, 'Historic', weight='100')
        # Roll the school onto a brand new active year.
        AcademicYear.objects.filter(pk=data.year.pk).update(is_active=False)
        year_c, term_c = extra_year(school_a, '2026/2027', active=True)
        ctx = context_for(teacher_a)
        assert ctx.year == year_c
        assessment.refresh_from_db()
        assert assessment.academic_year_id == data.year.pk
        assert assessment.term_id == data.term.pk


# --------------------------------------------------------------------------- #
# Override authorization + audit
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestOverrideControl:
    def test_teacher_cannot_reach_override_view(self, client, teacher_a, school_a):
        build_school(school_a, 'O1')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:academic_year_override'))
        assert resp.status_code == 302
        assert not AcademicYearOverride.objects.exists()

    def test_service_rejects_teacher_enable(self, school_a, teacher_a):
        build_school(school_a, 'O2')
        year_b, term_b = extra_year(school_a, '2015/2016')
        with pytest.raises(ay.AcademicYearError):
            ay.enable_override(user=teacher_a, school=school_a,
                               academic_year=year_b, term=term_b, reason='nope')

    def test_head_can_enable_and_disable(self, client, head_a, teacher_a, school_a):
        data = build_school(school_a, 'O3')
        year_b, term_b = extra_year(school_a, '2014/2015')
        client.force_login(head_a)
        resp = client.get(reverse('academics:academic_year_override'))
        assert resp.status_code == 200

        resp = client.post(reverse('academics:academic_year_override'), {
            'action': 'enable', 'academic_year': year_b.pk, 'term': term_b.pk,
            'reason': 'Back-filling term results', 'applies_to_all': 'on',
        })
        assert resp.status_code == 302
        override = AcademicYearOverride.objects.get(school=school_a)
        assert override.enabled_by == head_a
        assert override.reason == 'Back-filling term results'
        assert override.is_active is True
        assert AuditLog.objects.filter(action='OVERRIDE_ENABLED').exists()

        # The teacher now works in the overridden year.
        assert context_for(teacher_a).year == year_b

        resp = client.post(reverse('academics:academic_year_override'), {
            'action': 'disable', 'override': override.pk, 'disabled_reason': 'Done',
        })
        assert resp.status_code == 302
        override.refresh_from_db()
        assert override.disabled_at is not None
        assert override.disabled_by == head_a
        assert context_for(teacher_a).year == data.year
        assert AuditLog.objects.filter(action='OVERRIDE_DISABLED').exists()

    def test_enable_requires_reason(self, client, head_a, school_a):
        build_school(school_a, 'O4')
        year_b, term_b = extra_year(school_a, '2013/2014')
        client.force_login(head_a)
        resp = client.post(reverse('academics:academic_year_override'), {
            'action': 'enable', 'academic_year': year_b.pk, 'term': term_b.pk,
            'reason': '', 'applies_to_all': 'on',
        })
        assert resp.status_code == 200
        assert not AcademicYearOverride.objects.exists()

    def test_second_override_rejected_while_active(self, head_a, school_a):
        build_school(school_a, 'O5')
        year_b, term_b = extra_year(school_a, '2012/2013')
        year_c, term_c = extra_year(school_a, '2011/2012')
        ay.enable_override(user=head_a, school=school_a, academic_year=year_b,
                           term=term_b, reason='first')
        with pytest.raises(ay.AcademicYearError):
            ay.enable_override(user=head_a, school=school_a,
                               academic_year=year_c, term=term_c, reason='second')

    def test_expired_override_no_longer_applies(self, school_a, teacher_a, head_a):
        data = build_school(school_a, 'O5b')
        year_b, term_b = extra_year(school_a, '2012/2013')
        override = ay.enable_override(
            user=head_a, school=school_a, academic_year=year_b, term=term_b,
            reason='Short', expires_at=timezone.now() - timedelta(minutes=1))
        assert override.is_expired is True
        assert context_for(teacher_a).year == data.year

    def test_override_does_not_bypass_permissions(self, client, head_a, teacher_a, school_a):
        data = build_school(school_a, 'O5')
        assign(teacher_a, data)
        year_b, term_b = extra_year(school_a, '2012/2013')
        # A different class the teacher is *not* assigned to in year B.
        other_class = SchoolClass.objects.create(school=school_a, name='Other', code='OTH')
        other_subject = Subject.objects.create(school=school_a, name='Science', code='SCI')
        ClassSubject.objects.create(school_class=other_class, subject=other_subject)
        assessment = Assessment.objects.create(
            academic_year=year_b, term=term_b, school_class=other_class,
            subject=other_subject, name='Locked', max_score=100, weight=100)
        ay.enable_override(user=head_a, school=school_a, academic_year=year_b,
                           term=term_b, reason='Backfill')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:mark_entry', args=[assessment.pk]))
        assert resp.status_code == 302

    def test_only_one_live_override_per_school(self, school_a, head_a):
        build_school(school_a, 'O6')
        year_b, term_b = extra_year(school_a, '2012/2013')
        first = ay.enable_override(user=head_a, school=school_a,
                                   academic_year=year_b, term=term_b, reason='one')
        first.disabled_at = timezone.now()
        first.save(update_fields=['disabled_at'])
        year_c, term_c = extra_year(school_a, '2011/2012')
        second = ay.enable_override(user=head_a, school=school_a,
                                    academic_year=year_c, term=term_c, reason='two')
        assert second.is_active is True
