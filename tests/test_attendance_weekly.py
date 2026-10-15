"""Weekly bulk attendance grid tests.

The grid is a convenience over the daily register: each cell becomes an
ordinary daily ``AttendanceRecord``. These tests assert that blanks are never
turned into "present", corrections are audited, missing/repeated absences are
flagged and that the grid is school-isolated.
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import AttendanceRecord
from apps.academics.services import attendance as attendance_service
from apps.schools.models import School

from tests.test_academics import build_school

User = get_user_model()

WEEK = date(2026, 3, 2)  # a Monday


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
def head_a(db, school_a):
    return User.objects.create_user(
        username='aw_head_a', email='awha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='aw_head_b', email='awhb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='aw_teacher_a', email='awta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


@pytest.mark.django_db
class TestWeeklyGridService:
    def test_writes_daily_records_and_skips_blank_cells(self, school_a, head_a):
        data = build_school(school_a, 'AW1', student_count=2)
        s1, s2 = data.students
        entries = [
            (WEEK, s1, 'PRESENT', ''),
            (WEEK, s2, 'ABSENT', 'sick'),
            # A blank cell must be skipped, never written as present.
            (WEEK + timedelta(days=1), s1, '', ''),
            (WEEK + timedelta(days=1), s2, 'LATE', ''),
        ]
        saved = attendance_service.save_weekly_grid(
            data.school_class, data.year, data.term, WEEK, entries, user=head_a)

        assert saved == 3
        assert AttendanceRecord.objects.filter(session__date=WEEK).count() == 2
        # The blank cell for s1 created no record at all.
        assert not AttendanceRecord.objects.filter(
            session__date=WEEK + timedelta(days=1), student=s1).exists()
        late = AttendanceRecord.objects.get(
            session__date=WEEK + timedelta(days=1), student=s2)
        assert late.status == AttendanceRecord.Status.LATE

    def test_correction_is_audited(self, school_a, head_a):
        data = build_school(school_a, 'AW2', student_count=1)
        student = data.students[0]
        attendance_service.save_weekly_grid(
            data.school_class, data.year, data.term, WEEK,
            [(WEEK, student, 'PRESENT', '')], user=head_a)
        attendance_service.save_weekly_grid(
            data.school_class, data.year, data.term, WEEK,
            [(WEEK, student, 'ABSENT', 'sick')], user=head_a,
            reason='Parent telephoned to explain the absence.')

        record = AttendanceRecord.objects.get(student=student)
        assert record.status == AttendanceRecord.Status.ABSENT
        assert record.corrections.count() == 1
        correction = record.corrections.first()
        assert correction.old_status == 'PRESENT'
        assert correction.new_status == 'ABSENT'
        assert 'Parent telephoned' in correction.reason

    def test_register_flags_missing_and_repeated_absence(self, school_a, head_a):
        data = build_school(school_a, 'AW3', student_count=1)
        student = data.students[0]
        entries = [
            (WEEK + timedelta(days=i), student, 'ABSENT', '') for i in range(3)
        ]
        attendance_service.save_weekly_grid(
            data.school_class, data.year, data.term, WEEK, entries, user=head_a)

        grid = attendance_service.weekly_register(
            data.school_class, data.year, data.term, WEEK)
        row = next(r for r in grid['rows'] if r['student'] == student)
        assert row['absent_count'] == 3
        assert row['repeated_absence'] is True
        # Thursday and Friday were never recorded — shown as missing, not absent.
        assert row['missing_count'] == 2
        assert grid['recorded_days'] == 3
        assert grid['total_days'] == 5

    def test_register_puts_repeated_absence_in_context(self, school_a, head_a):
        data = build_school(school_a, 'AW4', student_count=1)
        student = data.students[0]
        # Two absences only: below the repeated-absence threshold.
        attendance_service.save_weekly_grid(
            data.school_class, data.year, data.term, WEEK,
            [(WEEK, student, 'ABSENT', ''),
             (WEEK + timedelta(days=1), student, 'ABSENT', '')], user=head_a)
        grid = attendance_service.weekly_register(
            data.school_class, data.year, data.term, WEEK)
        row = next(r for r in grid['rows'] if r['student'] == student)
        assert row['absent_count'] == 2
        assert row['repeated_absence'] is False


@pytest.mark.django_db
class TestWeeklyGridView:
    def test_get_renders(self, client, school_a, head_a):
        data = build_school(school_a, 'AWV1', student_count=2)
        client.force_login(head_a)
        resp = client.get(reverse('academics:attendance_weekly'), {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'week': WEEK.isoformat(),
        })
        assert resp.status_code == 200

    def test_post_saves_daily_records(self, client, school_a, head_a):
        data = build_school(school_a, 'AWV2', student_count=1)
        student = data.students[0]
        client.force_login(head_a)
        key = WEEK.strftime('%Y%m%d')
        resp = client.post(reverse('academics:attendance_weekly'), {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'week': WEEK.isoformat(),
            'session_label': 'Daily',
            'days': attendance_service.WEEK_DAYS,
            f'status_{student.pk}_{key}': 'LATE',
        })
        assert resp.status_code == 302
        record = AttendanceRecord.objects.get(
            student=student, session__date=WEEK)
        assert record.status == AttendanceRecord.Status.LATE
        # The record belongs to the current school.
        assert record.session.school_id == school_a.pk

    def test_cross_school_class_is_denied(self, client, school_a, head_b):
        data = build_school(school_a, 'AWV3', student_count=1)
        client.force_login(head_b)
        resp = client.get(reverse('academics:attendance_weekly'), {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'week': WEEK.isoformat(),
        })
        # The other school's class is not in head_b's visible set.
        assert resp.status_code == 302
        assert AttendanceRecord.objects.count() == 0

    def test_unassigned_teacher_cannot_take_attendance(
            self, client, school_a, teacher_a):
        data = build_school(school_a, 'AWV4', student_count=1)
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:attendance_weekly'), {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'week': WEEK.isoformat(),
        })
        assert resp.status_code == 302
