"""Academic module tests: isolation, permissions, attendance, marking and reports."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import (
    AcademicSettings,
    Assessment,
    AttendanceRecord,
    AttendanceSession,
    ClassSubject,
    GradeBand,
    GradingScale,
    MarkSubmission,
    ResultPublication,
    StudentMark,
    Subject,
    TeacherAssignment,
    TerminalComment,
    TerminalReportField,
)
from apps.academics.services import attendance as attendance_service
from apps.academics.services import marks as marks_service
from apps.academics.services import reports as reports_service
from apps.academics.services.grading import resolve_scale, subject_result
from apps.school_fees.models import (
    AcademicYear,
    FeeCategory,
    FeeStructure,
    SchoolClass,
    Student,
    Term,
)
from apps.school_fees.services import accounts as account_service
from apps.school_fees.services import payments as payment_service
from apps.schools.models import School

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
        username='acad_admin', email='a@test.com', password='x',
        first_name='Ada', last_name='Admin', role='SUPER_ADMIN', is_staff=True)


@pytest.fixture
def head_a(db, school_a):
    return User.objects.create_user(
        username='head_a', email='ha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='head_b', email='hb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='teacher_a', email='ta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


def build_school(school, suffix, student_count=2, fees='100.00'):
    """Create an academic + fees scaffold for one school."""
    school_class = SchoolClass.objects.create(
        school=school, name=f'Class {suffix}', code=f'C-{suffix}')
    year = AcademicYear.objects.create(school=school, name=f'2025/2026 {suffix}')
    term = Term.objects.create(academic_year=year, name='First Term', term_number=1)
    subject = Subject.objects.create(school=school, name=f'Maths {suffix}', code=f'M-{suffix}')
    class_subject = ClassSubject.objects.create(school_class=school_class, subject=subject)
    category = FeeCategory.objects.create(school=school, name=f'Tuition {suffix}', code=f'T-{suffix}')
    FeeStructure.objects.create(
        academic_year=year, term=term, school_class=school_class,
        fee_category=category, amount=Decimal(fees),
        due_date=date(2026, 1, 31))
    for key, _label in TerminalReportField.FieldKey.choices:
        TerminalReportField.objects.create(school=school, key=key, is_visible=True)
    students = []
    accounts = []
    for i in range(student_count):
        student = Student.objects.create(
            school=school, first_name=f'Pupil{i}', last_name=suffix,
            school_class=school_class, parent_name='Parent', parent_phone='0240000000')
        students.append(student)
        accounts.append(account_service.get_or_create_fee_account(student, term))
    scale = GradingScale.objects.create(school=school, name=f'Scale {suffix}', is_default=True)
    GradeBand.objects.create(scale=scale, grade='A', min_score=70, max_score=100, remark='Excellent')
    GradeBand.objects.create(scale=scale, grade='B',
                             description='Very good', min_score=60, max_score=69.99)
    GradeBand.objects.create(scale=scale, grade='C', description='Credit', min_score=50, max_score=59.99)
    GradeBand.objects.create(scale=scale, grade='F', description='Fail', min_score=0, max_score=49.99)
    settings_obj = AcademicSettings.for_school(school)
    settings_obj.default_grading_scale = scale
    settings_obj.show_position = True
    settings_obj.save()
    return SimpleNamespace(
        school=school, school_class=school_class, year=year, term=term, subject=subject,
        class_subject=class_subject, category=category, students=students,
        accounts=accounts, scale=scale)


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestGrading:
    def test_weighted_percentage_uses_configured_weights(self, school_a):
        data = build_school(school_a, 'G1', student_count=1)
        student = data.students[0]
        a1 = build_assessment(data, 'Test', weight='40', max_score='100')
        a2 = build_assessment(data, 'Exam', weight='60', max_score='100')
        marks_service.save_marks(a1, [(student, '50', 'PRESENT')], None)
        marks_service.save_marks(a2, [(student, '80', 'PRESENT')], None)
        result = subject_result(student, data.year, data.term, data.subject, resolve_scale(school_a))
        # 0.5*40 + 0.8*60 = 68 -> normalised by 100 = 68%
        assert result['percentage'] == Decimal('68.00')
        assert result['grade'] is not None

    def test_absent_is_not_treated_as_zero(self, school_a):
        data = build_school(school_a, 'G2', student_count=1)
        student = data.students[0]
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        marks_service.save_marks(assessment, [(student, None, 'ABSENT')], None)
        result = subject_result(student, data.year, data.term, data.subject, resolve_scale(school_a))
        assert result['percentage'] is None
        assert result['has_absent'] is True

    def test_missing_mark_flags_incomplete(self, school_a):
        data = build_school(school_a, 'G3', student_count=1)
        student = data.students[0]
        build_assessment(data, 'Test', weight='100', max_score='100')
        result = subject_result(student, data.year, data.term, data.subject, resolve_scale(school_a))
        assert result['incomplete'] is True
        assert result['percentage'] is None


def build_assessment(data, name, weight='100', max_score='100'):
    return Assessment.objects.create(
        academic_year=data.year, term=data.term, school_class=data.school_class,
        subject=data.subject, name=name, max_score=Decimal(max_score),
        weight=Decimal(weight))


# --------------------------------------------------------------------------- #
# Isolation & permissions
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestIsolation:
    def test_head_sees_only_own_subjects(self, client, head_a, head_b, school_a, school_b):
        build_school(school_a, 'I1')
        build_school(school_b, 'I2')
        client.force_login(head_a)
        resp = client.get(reverse('academics:subject_list'))
        assert resp.status_code == 200
        subjects = list(resp.context['subjects'])
        assert subjects and all(s.school_id == school_a.pk for s in subjects)

    def test_teacher_cannot_open_other_school_assessment(self, client, teacher_a, school_b):
        b = build_school(school_b, 'I3')
        assessment = build_assessment(b, 'Hidden', weight='100')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:mark_entry', args=[assessment.pk]))
        assert resp.status_code == 302

    def test_teacher_cannot_mark_unassigned_class(self, client, teacher_a, school_a, head_a):
        data = build_school(school_a, 'I4')
        assessment = build_assessment(data, 'Test', weight='100')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:mark_entry', args=[assessment.pk]))
        assert resp.status_code == 302

    def test_teacher_can_mark_assigned_class(self, client, teacher_a, school_a):
        data = build_school(school_a, 'I5')
        TeacherAssignment.objects.create(
            teacher=teacher_a, school_class=data.school_class, subject=data.subject,
            academic_year=data.year)
        assessment = build_assessment(data, 'Test', weight='100')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:mark_entry', args=[assessment.pk]))
        assert resp.status_code == 200

    def test_school_bound_users_can_open_assessment_list(
            self, client, school_a, head_a, teacher_a):
        """Terms have no ``school``; they must be scoped via their year.

        Regression test: the assessments list scoped terms by a ``school``
        field, which raised ``FieldError`` for school-bound (non-global) users.
        """
        build_school(school_a, 'I6')

        client.force_login(head_a)
        assert client.get(reverse('academics:assessment_list')).status_code == 200

        client.force_login(teacher_a)
        assert client.get(reverse('academics:assessment_list')).status_code == 200


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestAttendance:
    def test_duplicate_session_prevented(self, school_a, teacher_a):
        data = build_school(school_a, 'A1', student_count=2)
        s1, created1 = attendance_service.get_or_create_session(
            school_class=data.school_class, subject=None, academic_year=data.year,
            term=data.term, date='2026-01-10', session_label='Daily', user=teacher_a)
        s2, created2 = attendance_service.get_or_create_session(
            school_class=data.school_class, subject=None, academic_year=data.year,
            term=data.term, date='2026-01-10', session_label='Daily', user=teacher_a)
        assert created1 is True and created2 is False
        assert s1.pk == s2.pk
        assert AttendanceSession.objects.count() == 1

    def test_correction_creates_audit_trail(self, school_a, teacher_a):
        data = build_school(school_a, 'A2', student_count=1)
        student = data.students[0]
        session, _ = attendance_service.get_or_create_session(
            school_class=data.school_class, subject=None, academic_year=data.year,
            term=data.term, date='2026-01-11', session_label='Daily', user=teacher_a)
        attendance_service.save_attendance(
            session, [(student, 'PRESENT', '')], user=teacher_a)
        attendance_service.save_attendance(
            session, [(student, 'ABSENT', 'sick')], user=teacher_a, reason='parent note')
        record = AttendanceRecord.objects.get(session=session, student=student)
        assert record.status == 'ABSENT'
        assert record.corrections.count() == 1
        assert record.corrections.first().old_status == 'PRESENT'

    def test_student_summary_percentage(self, school_a, teacher_a):
        data = build_school(school_a, 'A3', student_count=2)
        s1, s2 = data.students
        session, _ = attendance_service.get_or_create_session(
            school_class=data.school_class, subject=None, academic_year=data.year,
            term=data.term, date='2026-01-12', session_label='Daily', user=teacher_a)
        attendance_service.save_attendance(
            session, [(s1, 'PRESENT', ''), (s2, 'ABSENT', '')], user=teacher_a)
        summary = attendance_service.student_summary(s1, data.year, data.term)
        assert summary['present'] == 1 and summary['total'] == 1
        summary2 = attendance_service.student_summary(s2, data.year, data.term)
        assert summary2['percentage'] == Decimal('0.0')


# --------------------------------------------------------------------------- #
# Marking workflow
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestMarkingWorkflow:
    def test_score_above_max_rejected(self, school_a):
        data = build_school(school_a, 'M1', student_count=1)
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        with pytest.raises(marks_service.MarkingError):
            marks_service.save_marks(assessment, [(data.students[0], '120', 'PRESENT')], None)

    def test_submit_review_and_lock_on_publish(self, school_a, head_a, admin_user):
        data = build_school(school_a, 'M2', student_count=1)
        student = data.students[0]
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        marks_service.save_marks(assessment, [(student, '70', 'PRESENT')], None)
        submission = marks_service.submit_for_review(
            data.year, data.term, data.school_class, data.subject, admin_user)
        assert submission.status == MarkSubmission.Status.SUBMITTED

        marks_service.review_submission(submission, True, head_a, 'well done')
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.APPROVED
        with pytest.raises(marks_service.MarkingError):
            marks_service.save_marks(assessment, [(student, '75', 'PRESENT')], None)

        marks_service.publish_results(data.year, data.term, data.school_class, head_a)
        assert ResultPublication.is_locked(data.year, data.term, data.school_class)
        with pytest.raises(marks_service.MarkingError):
            marks_service.save_marks(assessment, [(student, '80', 'PRESENT')], None)

        # Unpublishing is a controlled correction and now requires a reason.
        with pytest.raises(marks_service.MarkingError):
            marks_service.unpublish_results(
                data.year, data.term, data.school_class, head_a)
        marks_service.unpublish_results(
            data.year, data.term, data.school_class, head_a,
            reason='Correcting a keying error reported by the teacher.')
        assert not ResultPublication.is_locked(data.year, data.term, data.school_class)

    def test_results_view_isolated_to_school(self, client, head_a, school_a):
        data = build_school(school_a, 'M3', student_count=1)
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        marks_service.save_marks(assessment, [(data.students[0], '55', 'PRESENT')], None)
        client.force_login(head_a)
        url = reverse('academics:results')
        resp = client.get(url, {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
        })
        assert resp.status_code == 200
        assert resp.context['school_class'].school_id == school_a.pk


# --------------------------------------------------------------------------- #
# Submission review workflow
# --------------------------------------------------------------------------- #
def submit_sample(data, teacher, score='70'):
    """Create an assessment + marks and submit it for review."""
    assessment = build_assessment(data, 'Test', weight='100', max_score='100')
    marks_service.save_marks(
        assessment, [(data.students[0], score, 'PRESENT')], teacher)
    submission = marks_service.submit_for_review(
        data.year, data.term, data.school_class, data.subject, teacher)
    return assessment, submission


@pytest.mark.django_db
class TestSubmissionReviewWorkflow:
    def test_review_page_shows_actions_and_student_names(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV1', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_a)
        resp = client.get(
            reverse('academics:submission_review', args=[submission.pk]))
        assert resp.status_code == 200
        assert resp.context['awaiting_review'] is True
        assert resp.context['can_review'] is True
        # Preview rows carry the actual student, not the subject name.
        assert resp.context['preview'][0]['student'].pk == data.students[0].pk
        html = resp.content.decode()
        assert 'Approve Results' in html
        assert 'Return for Corrections' in html
        assert data.students[0].last_name in html

    def test_head_approves_submission(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV2', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'approve'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.APPROVED
        assert submission.reviewed_by == head_a
        assert submission.reviewed_at is not None

    def test_return_requires_feedback(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV3', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_a)
        url = reverse('academics:submission_review', args=[submission.pk])

        # Empty feedback is rejected and the submission stays awaiting review.
        resp = client.post(url, {'action': 'return', 'comment': '   '})
        assert resp.status_code == 200
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED

        # Meaningful feedback returns the submission to the teacher.
        resp = client.post(url, {'action': 'return', 'comment': 'Recheck question 3.'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.RETURNED
        assert submission.review_comment == 'Recheck question 3.'
        assert submission.reviewed_by == head_a

    def test_returned_submission_can_be_edited_and_resubmitted(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV4', student_count=1)
        student = data.students[0]
        assessment, submission = submit_sample(data, teacher_a)
        TeacherAssignment.objects.create(
            teacher=teacher_a, school_class=data.school_class,
            subject=data.subject, academic_year=data.year)
        marks_service.review_submission(
            submission, False, head_a, 'Correct question 3.')
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.RETURNED

        client.force_login(teacher_a)
        resp = client.post(
            reverse('academics:mark_entry', args=[assessment.pk]),
            {f'score_{student.pk}': '80', f'status_{student.pk}': 'PRESENT',
             'submit': '1'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED
        assert StudentMark.objects.get(
            assessment=assessment, student=student).score == Decimal('80.00')

    def test_teacher_cannot_open_review(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV5', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(teacher_a)
        resp = client.get(
            reverse('academics:submission_review', args=[submission.pk]))
        assert resp.status_code == 302
        assert resp.url == reverse('dashboard')

    def test_cross_school_head_cannot_review(
            self, client, school_a, school_b, head_b, teacher_a):
        data = build_school(school_a, 'RV6', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_b)
        resp = client.get(
            reverse('academics:submission_review', args=[submission.pk]))
        assert resp.status_code == 302
        # The forbidden POST cannot change the submission either.
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'approve'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED

    def test_submitter_cannot_review_own_submission(
            self, client, school_a, head_a):
        data = build_school(school_a, 'RV7', student_count=1)
        _assessment, submission = submit_sample(data, head_a)
        assert submission.submitted_by == head_a
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'approve'})
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED

    def test_cannot_review_when_not_awaiting_review(
            self, client, school_a, head_a, admin_user, teacher_a):
        data = build_school(school_a, 'RV8', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        marks_service.review_submission(submission, True, head_a, 'fine')
        client.force_login(admin_user)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'return', 'comment': 'changed my mind'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.APPROVED
        assert submission.review_comment == 'fine'

    def test_unknown_submission_id_is_not_found(
            self, client, school_a, head_a):
        build_school(school_a, 'RV9', student_count=1)
        client.force_login(head_a)
        resp = client.get(
            reverse('academics:submission_review', args=[999999]))
        assert resp.status_code == 404

    def test_unknown_action_is_rejected(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RV10', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'delete-everything'})
        assert resp.status_code == 200
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED


# --------------------------------------------------------------------------- #
# Flexible date input
# --------------------------------------------------------------------------- #
COMMON_DATE_INPUTS = (
    '2026-12-10', '10/12/2026', '10-12-2026', '10.12.2026',
    '10 Dec 2026', '10 December 2026', 'Dec 10, 2026',
)


@pytest.mark.django_db
class TestFlexibleDateInput:
    def test_calendar_form_accepts_common_formats(self, school_a):
        from apps.academics.forms import AcademicCalendarEventForm
        for raw in COMMON_DATE_INPUTS:
            form = AcademicCalendarEventForm(data={
                'school': school_a.pk, 'title': 'Event', 'event_type': 'EVENT',
                'start_date': raw, 'end_date': raw, 'description': '',
                'is_all_day': True,
            })
            assert form.is_valid(), (raw, form.errors.as_json())
            assert form.cleaned_data['start_date'] == date(2026, 12, 10)
            assert form.cleaned_data['end_date'] == date(2026, 12, 10)

    def test_assessment_form_accepts_common_formats(self, school_a):
        from apps.academics.forms import AssessmentForm
        data = build_school(school_a, 'D2')
        for raw in COMMON_DATE_INPUTS:
            form = AssessmentForm(data={
                'academic_year': data.year.pk, 'term': data.term.pk,
                'school_class': data.school_class.pk, 'subject': data.subject.pk,
                'name': 'Test', 'assessment_type': 'EXAM', 'max_score': '100',
                'weight': '100', 'date': raw, 'marks_due_date': raw,
                'is_active': True,
            })
            assert form.is_valid(), (raw, form.errors.as_json())
            assert form.cleaned_data['date'] == date(2026, 12, 10)
            assert form.cleaned_data['marks_due_date'] == date(2026, 12, 10)

    def test_date_fields_show_format_hint(self):
        from apps.academics.forms import (
            AcademicCalendarEventForm, AssessmentForm, DATE_PLACEHOLDER)
        for form in (AcademicCalendarEventForm(), AssessmentForm()):
            for name in ('start_date', 'end_date', 'date', 'marks_due_date'):
                field = form.fields.get(name)
                if field is not None:
                    assert field.widget.attrs.get('placeholder') == DATE_PLACEHOLDER

    def test_calendar_form_rejects_invalid_and_reversed_dates(self, school_a):
        from apps.academics.forms import AcademicCalendarEventForm
        bad = AcademicCalendarEventForm(data={
            'school': school_a.pk, 'title': 'Event', 'event_type': 'EVENT',
            'start_date': 'not-a-date', 'description': '', 'is_all_day': True,
        })
        assert not bad.is_valid()
        assert 'start_date' in bad.errors

        reversed_dates = AcademicCalendarEventForm(data={
            'school': school_a.pk, 'title': 'Event', 'event_type': 'EVENT',
            'start_date': '15/10/2026', 'end_date': '12/10/2026',
            'description': '', 'is_all_day': True,
        })
        assert not reversed_dates.is_valid()
        assert 'end_date' in reversed_dates.errors


# --------------------------------------------------------------------------- #
# Terminal report & fees reconciliation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestTerminalReport:
    def test_financial_summary_matches_fee_account(self, school_a, admin_user):
        data = build_school(school_a, 'R1', student_count=1, fees='250.00')
        student = data.students[0]
        account = account_service.get_or_create_fee_account(student, data.term)
        account.refresh_from_db()
        payment_service.record_payment(account, Decimal('100.00'), recorded_by=admin_user)
        account.refresh_from_db()

        finance = reports_service.financial_summary(student, data.year, data.term)
        assert finance['total_fees'] == account.total_fees
        assert finance['amount_paid'] == account.amount_paid
        assert finance['outstanding'] == account.total_fees - account.amount_paid

    def test_arrears_from_earlier_term(self, school_a, admin_user):
        data = build_school(school_a, 'R2', student_count=1, fees='100.00')
        student = data.students[0]
        # Prior term with an unpaid balance.
        prior_term = Term.objects.create(
            academic_year=data.year, name='Earlier Term', term_number=0)
        FeeStructure.objects.create(
            academic_year=data.year, term=prior_term, school_class=data.school_class,
            fee_category=FeeCategory.objects.get(school=school_a, code='T-R2'),
            amount=Decimal('60.00'), due_date=date(2025, 9, 30))
        prior_account = account_service.get_or_create_fee_account(student, prior_term)
        prior_account.refresh_from_db()

        finance = reports_service.financial_summary(student, data.year, data.term)
        assert finance['arrears'] == prior_account.total_fees

    def test_report_fields_flag_unmodelled_values(self, school_a):
        data = build_school(school_a, 'R3', student_count=1)
        student = data.students[0]
        account_service.get_or_create_fee_account(student, data.term)
        finance = reports_service.financial_summary(student, data.year, data.term)
        rows = reports_service.report_fields(school_a, finance)
        flags = {row['key']: row['flag'] for row in rows}
        assert flags.get('TOTAL_FEES', '') == ''
        # Discounts are not modelled by the ledger and must be flagged, not 0.
        assert flags.get('DISCOUNT') == 'Not configured in the fees module'

    def test_terminal_report_data_assembles_academics_and_fees(self, school_a):
        data = build_school(school_a, 'R4', student_count=1, fees='150.00')
        student = data.students[0]
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        marks_service.save_marks(assessment, [(student, '60', 'PRESENT')], None)
        payload = reports_service.terminal_report_data(student, data.year, data.term)
        assert payload['student'] == student
        assert payload['results']['average'] is not None
        assert payload['finance']['total_fees'] == Decimal('150.00')
        assert payload['report_fields']


# --------------------------------------------------------------------------- #
# Dashboard routing
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRouting:
    def test_teacher_redirected_to_teacher_dashboard(self, client, teacher_a):
        client.force_login(teacher_a)
        resp = client.get(reverse('dashboard'))
        assert resp.status_code == 302
        assert resp.url == reverse('academics:teacher_dashboard')

    def test_teacher_dashboard_renders(self, client, teacher_a, school_a):
        build_school(school_a, 'RT', student_count=1)
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:teacher_dashboard'))
        assert resp.status_code == 200

    def test_cashier_forbidden_from_academics(self, client, cashier_user):
        client.force_login(cashier_user)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.status_code == 302


# --------------------------------------------------------------------------- #
# Page rendering smoke tests (catch template/URL errors)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestPageSmoke:
    def test_config_pages_render(self, client, school_a, admin_user):
        data = build_school(school_a, 'SM1', student_count=1)
        client.force_login(admin_user)
        home = client.get(reverse('academics:home'))
        assert home.status_code == 302
        assert home.url == reverse('academics:head_dashboard')
        urls = [
            reverse('academics:head_dashboard'),
            reverse('academics:subject_list'),
            reverse('academics:subject_create'),
            reverse('academics:class_subject_list'),
            reverse('academics:class_subject_create'),
            reverse('academics:teacher_assignment_list'),
            reverse('academics:teacher_assignment_create'),
            reverse('academics:grading_scale_list'),
            reverse('academics:grading_scale_create'),
            reverse('academics:grading_scale_edit', args=[data.scale.pk]),
            reverse('academics:academic_settings'),
            reverse('academics:report_fields'),
            reverse('academics:assessment_list'),
            reverse('academics:assessment_create'),
            reverse('academics:attendance_index'),
            reverse('academics:submissions'),
            reverse('academics:update_request_list'),
            reverse('academics:update_request_create'),
            reverse('academics:calendar'),
            reverse('academics:calendar_create'),
            reverse('academics:announcements'),
            reverse('academics:announcement_create'),
        ]
        for url in urls:
            resp = client.get(url)
            assert resp.status_code == 200, f'{url} -> {resp.status_code}'

    def test_academic_pages_and_report_render(self, client, admin_user, school_a):
        data = build_school(school_a, 'SM2', student_count=2)
        admin = admin_user
        assessment = build_assessment(data, 'Test', weight='100')
        marks_service.save_marks(
            assessment, [(data.students[0], '64', 'PRESENT')], admin)
        client.force_login(admin)
        params = {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
        }

        assert client.get(
            reverse('academics:mark_entry', args=[assessment.pk])).status_code == 200
        assert client.get(reverse('academics:results'), params).status_code == 200
        assert client.get(reverse('academics:attendance_report'), params).status_code == 200

        take_url = reverse('academics:attendance_take')
        assert client.get(take_url, {**params, 'date': '2026-02-01'}).status_code == 200

        promote_url = reverse('academics:promote', args=[data.students[0].pk])
        assert client.get(promote_url, params).status_code == 200

        report_url = reverse('academics:terminal_report', args=[data.students[0].pk])
        assert client.get(report_url, params).status_code == 200

        pdf = client.get(
            reverse('academics:terminal_report_pdf'),
            {'student': data.students[0].pk, **params})
        assert pdf.status_code == 200
        assert pdf['Content-Type'] == 'application/pdf'

    def test_mark_entry_and_attendance_post(self, client, admin_user, school_a):
        data = build_school(school_a, 'SM3', student_count=1)
        student = data.students[0]
        assessment = build_assessment(data, 'Test', weight='100')
        client.force_login(admin_user)

        resp = client.post(reverse('academics:mark_entry', args=[assessment.pk]), {
            f'score_{student.pk}': '77',
            f'status_{student.pk}': 'PRESENT',
        })
        assert resp.status_code == 302
        mark = StudentMark.objects.get(assessment=assessment, student=student)
        assert mark.score == Decimal('77.00')
        assert mark.status == 'PRESENT'

        session_url = reverse('academics:attendance_take')
        resp = client.post(session_url, {
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'date': '2026-02-02',
            'session_label': 'Daily',
            f'status_{student.pk}': 'ABSENT',
        })
        assert resp.status_code == 302
        record = AttendanceRecord.objects.get(student=student, session__date='2026-02-02')
        assert record.status == 'ABSENT'
