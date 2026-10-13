"""Head Teacher Supervision tests: update requests and controlled corrections.

These cover the Requests & Follow-ups centre (creation, transitions, history,
school isolation and role permissions) and the controlled correction of
approved / published results, plus the real dashboard aggregates that feed the
head and teacher dashboards.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import (
    MarkCorrection,
    MarkSubmission,
    ResultPublication,
    UpdateRequest,
    UpdateRequestEvent,
)
from apps.academics.services import marks as marks_service
from apps.academics.services import requests as requests_service
from apps.audit.models import AuditLog
from apps.schools.models import School

from tests.test_academics import build_assessment, build_school, submit_sample

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
        username='req_admin', email='ra@test.com', password='x',
        first_name='Ada', last_name='Admin', role='SUPER_ADMIN', is_staff=True)


@pytest.fixture
def head_a(db, school_a):
    return User.objects.create_user(
        username='req_head_a', email='rha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='req_head_b', email='rhb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='req_teacher_a', email='rta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


@pytest.fixture
def teacher_a2(db, school_a):
    return User.objects.create_user(
        username='req_teacher_a2', email='rta2@test.com', password='x',
        first_name='Tee', last_name='Two', role='TEACHER', school=school_a)


@pytest.fixture
def teacher_b(db, school_b):
    return User.objects.create_user(
        username='req_teacher_b', email='rtb@test.com', password='x',
        first_name='Tee', last_name='B', role='TEACHER', school=school_b)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_request(school, requested_by, assigned_to, **overrides):
    """Create an update request via the service with sensible defaults."""
    kwargs = dict(
        school=school,
        title='Follow up on records',
        category=UpdateRequest.Category.OTHER,
        description='',
        assigned_to=assigned_to,
        priority=UpdateRequest.Priority.MEDIUM,
    )
    kwargs.update(overrides)
    return requests_service.create_update_request(requested_by, **kwargs)


def approve_sample(data, teacher, head, score='70'):
    """Create + mark + submit + approve a submission for the block."""
    assessment = build_assessment(data, 'Test', weight='100', max_score='100')
    marks_service.save_marks(
        assessment, [(data.students[0], score, 'PRESENT')], teacher)
    submission = marks_service.submit_for_review(
        data.year, data.term, data.school_class, data.subject, teacher)
    marks_service.review_submission(submission, True, head, 'ok')
    submission.refresh_from_db()
    return assessment, submission


# --------------------------------------------------------------------------- #
# Request centre service
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRequestService:
    def test_create_records_history_and_audit(
            self, school_a, head_a, teacher_a):
        req = make_request(school_a, head_a, teacher_a, title='Cover a class')
        assert req.school_id == school_a.pk
        assert req.requested_by_id == head_a.pk
        assert req.assigned_to_id == teacher_a.pk
        actions = set(req.events.values_list('action', flat=True))
        assert UpdateRequestEvent.Action.CREATED in actions
        assert UpdateRequestEvent.Action.ASSIGNED in actions
        assert AuditLog.objects.filter(
            action=AuditLog.ActionType.REQUEST_CREATED).count() == 1

    def test_create_rejects_cross_school_teacher(
            self, school_a, head_a, teacher_b):
        with pytest.raises(requests_service.RequestError):
            make_request(school_a, head_a, teacher_b)

    def test_create_rejects_non_teacher_assignee(
            self, school_a, head_a):
        with pytest.raises(requests_service.RequestError):
            make_request(school_a, head_a, head_a)

    def test_create_requires_school(self, head_a, teacher_a):
        with pytest.raises(requests_service.RequestError):
            requests_service.create_update_request(
                head_a, school=None, title='x',
                category=UpdateRequest.Category.OTHER, description='',
                assigned_to=teacher_a, priority=UpdateRequest.Priority.LOW)

    def test_teacher_start_then_submit_records_response(
            self, school_a, head_a, teacher_a):
        req = make_request(school_a, head_a, teacher_a)
        requests_service.transition(req, teacher_a, 'IN_PROGRESS')
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.IN_PROGRESS

        requests_service.transition(
            req, teacher_a, 'SUBMITTED', notes='All marks verified.')
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.SUBMITTED
        assert req.response == 'All marks verified.'
        assert req.submitted_at is not None
        assert req.events.filter(
            action=UpdateRequestEvent.Action.SUBMITTED).exists()

    def test_teacher_cannot_complete(self, school_a, head_a, teacher_a):
        req = make_request(school_a, head_a, teacher_a)
        with pytest.raises(requests_service.RequestError):
            requests_service.transition(req, teacher_a, 'COMPLETED')

    def test_manager_confirms_and_reopens(
            self, school_a, head_a, teacher_a):
        req = make_request(school_a, head_a, teacher_a)
        requests_service.transition(req, teacher_a, 'IN_PROGRESS')
        requests_service.transition(req, teacher_a, 'SUBMITTED', notes='done')

        requests_service.transition(req, head_a, 'COMPLETED')
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.COMPLETED
        assert req.completed_at is not None

        requests_service.transition(req, head_a, 'PENDING')
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.PENDING
        assert req.completed_at is None
        assert req.events.filter(
            action=UpdateRequestEvent.Action.REOPENED).exists()

    def test_comment_permissions(self, school_a, head_a, teacher_a, teacher_b):
        req = make_request(school_a, head_a, teacher_a)
        requests_service.add_comment(req, teacher_a, 'Working on it.')
        assert req.events.filter(
            action=UpdateRequestEvent.Action.COMMENTED).exists()

        with pytest.raises(requests_service.RequestError):
            requests_service.add_comment(req, teacher_b, 'Not my job.')
        with pytest.raises(requests_service.RequestError):
            requests_service.add_comment(req, teacher_a, '   ')

    def test_is_overdue_only_while_open(
            self, school_a, head_a, teacher_a):
        yesterday = date.today() - timedelta(days=1)
        req = make_request(school_a, head_a, teacher_a, due_date=yesterday)
        assert req.is_overdue is True

        requests_service.transition(req, teacher_a, 'IN_PROGRESS')
        req.refresh_from_db()
        assert req.is_overdue is True

        requests_service.transition(req, teacher_a, 'SUBMITTED', notes='done')
        req.refresh_from_db()
        assert req.is_overdue is False

    def test_unknown_status_rejected(self, school_a, head_a, teacher_a):
        req = make_request(school_a, head_a, teacher_a)
        with pytest.raises(requests_service.RequestError):
            requests_service.transition(req, head_a, 'NONSENSE')


# --------------------------------------------------------------------------- #
# Request centre views & permissions
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRequestViews:
    def test_head_creates_request_through_view(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'RQ1', student_count=1)
        client.force_login(head_a)
        resp = client.post(reverse('academics:update_request_create'), {
            'title': 'Fix attendance register',
            'category': UpdateRequest.Category.ATTENDANCE_MISSING,
            'priority': UpdateRequest.Priority.HIGH,
            'description': 'Term one register is incomplete.',
            'assigned_to': teacher_a.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'school_class': data.school_class.pk,
            'subject': data.subject.pk,
        })
        assert resp.status_code == 302
        req = UpdateRequest.objects.get(title='Fix attendance register')
        assert req.school_id == school_a.pk
        assert req.assigned_to_id == teacher_a.pk
        assert req.school_class_id == data.school_class.pk

    def test_teacher_cannot_create_request(self, client, teacher_a):
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:update_request_create'))
        assert resp.status_code == 302
        assert resp.url == reverse('dashboard')

    def test_cashier_forbidden_from_request_centre(self, client, cashier_user):
        client.force_login(cashier_user)
        resp = client.get(reverse('academics:update_request_list'))
        assert resp.status_code == 302
        assert resp.url == reverse('dashboard')

    def test_teacher_sees_only_own_requests(
            self, client, school_a, head_a, teacher_a, teacher_a2):
        build_school(school_a, 'RQ2', student_count=1)
        mine = make_request(school_a, head_a, teacher_a, title='Mine')
        other = make_request(school_a, head_a, teacher_a2, title='Someone else')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:update_request_list'))
        assert resp.status_code == 200
        ids = {r.pk for r in resp.context['requests']}
        assert mine.pk in ids
        assert other.pk not in ids

    def test_manager_sees_all_school_requests(
            self, client, school_a, head_a, teacher_a, teacher_a2):
        build_school(school_a, 'RQ3', student_count=1)
        one = make_request(school_a, head_a, teacher_a, title='One')
        two = make_request(school_a, head_a, teacher_a2, title='Two')
        client.force_login(head_a)
        resp = client.get(reverse('academics:update_request_list'))
        ids = {r.pk for r in resp.context['requests']}
        assert {one.pk, two.pk} <= ids

    def test_school_requests_are_isolated(
            self, client, school_a, school_b, head_a, head_b, teacher_a, teacher_b):
        build_school(school_a, 'RQ4', student_count=1)
        build_school(school_b, 'RQ5', student_count=1)
        mine = make_request(school_a, head_a, teacher_a, title='School A')
        theirs = make_request(school_b, head_b, teacher_b, title='School B')

        client.force_login(head_a)
        resp = client.get(reverse('academics:update_request_list'))
        ids = {r.pk for r in resp.context['requests']}
        assert mine.pk in ids
        assert theirs.pk not in ids

        # Cross-school detail access is refused.
        resp = client.get(
            reverse('academics:update_request_detail', args=[theirs.pk]))
        assert resp.status_code == 302
        assert resp.url == reverse('academics:update_request_list')

    def test_teacher_cannot_touch_unassigned_request(
            self, client, school_a, head_a, teacher_a, teacher_a2):
        build_school(school_a, 'RQ6', student_count=1)
        req = make_request(school_a, head_a, teacher_a)
        client.force_login(teacher_a2)
        resp = client.post(
            reverse('academics:update_request_detail', args=[req.pk]),
            {'action': 'transition', 'to_status': 'IN_PROGRESS'})
        assert resp.status_code == 302
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.PENDING

    def test_detail_renders_and_transition_flow(
            self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'RQ7', student_count=1)
        req = make_request(school_a, head_a, teacher_a)
        detail = reverse('academics:update_request_detail', args=[req.pk])

        client.force_login(head_a)
        assert client.get(
            reverse('academics:update_request_list')).status_code == 200
        resp = client.get(reverse('academics:update_request_detail', args=[req.pk]))
        assert resp.status_code == 200
        assert resp.context['request_obj'].pk == req.pk

        client.force_login(teacher_a)
        # Submit without a response is rejected.
        resp = client.post(
            reverse('academics:update_request_detail', args=[req.pk]),
            {'action': 'transition', 'to_status': 'SUBMITTED'})
        assert resp.status_code == 200
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.PENDING

        # Submitting with a response works and is stored.
        resp = client.post(
            reverse('academics:update_request_detail', args=[req.pk]),
            {'action': 'transition', 'to_status': 'SUBMITTED',
             'response-notes': 'Everything is complete.'})
        assert resp.status_code == 302
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.SUBMITTED
        assert req.response == 'Everything is complete.'

    def test_comment_via_view(self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'RQ7b', student_count=1)
        req = make_request(school_a, head_a, teacher_a)
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:update_request_detail', args=[req.pk]),
            {'action': 'comment', 'comment-comment': 'Please prioritise this.'})
        assert resp.status_code == 302
        assert req.events.filter(
            action=UpdateRequestEvent.Action.COMMENTED,
            notes='Please prioritise this.').exists()

    def test_manager_confirms_via_view(self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'RQ7c', student_count=1)
        req = make_request(school_a, head_a, teacher_a)
        requests_service.transition(req, teacher_a, 'IN_PROGRESS')
        requests_service.transition(req, teacher_a, 'SUBMITTED', notes='done')
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:update_request_detail', args=[req.pk]),
            {'action': 'transition', 'to_status': 'COMPLETED'})
        assert resp.status_code == 302
        req.refresh_from_db()
        assert req.status == UpdateRequest.Status.COMPLETED


# --------------------------------------------------------------------------- #
# Controlled corrections
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestControlledCorrections:
    def test_approved_marks_are_not_silently_editable(
            self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C1', student_count=1)
        student = data.students[0]
        assessment, submission = approve_sample(data, teacher_a, head_a)

        with pytest.raises(marks_service.MarkingError):
            marks_service.save_marks(
                assessment, [(student, '90', 'PRESENT')], teacher_a)

        correction = marks_service.open_mark_correction(
            data.year, data.term, data.school_class, submission=submission,
            user=head_a, reason='Mark belonged to another student')
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.DRAFT

        marks_service.save_marks(
            assessment, [(student, '90', 'PRESENT')], teacher_a)
        correction.refresh_from_db()
        assert correction.changes
        recorded = correction.changes[0]
        assert Decimal(recorded['old']['score']) == Decimal('70')
        assert Decimal(recorded['new']['score']) == Decimal('90')

    def test_open_correction_requires_reason(
            self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C2', student_count=1)
        _assessment, submission = approve_sample(data, teacher_a, head_a)
        with pytest.raises(marks_service.MarkingError):
            marks_service.open_mark_correction(
                data.year, data.term, data.school_class, submission=submission,
                user=head_a, reason='   ')

    def test_open_correction_rejects_unapproved_submission(
            self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C3', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        with pytest.raises(marks_service.MarkingError):
            marks_service.open_mark_correction(
                data.year, data.term, data.school_class, submission=submission,
                user=head_a, reason='too early')

    def test_reopened_marks_resubmitted_close_correction(
            self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C4', student_count=1)
        student = data.students[0]
        assessment, submission = approve_sample(data, teacher_a, head_a)
        marks_service.open_mark_correction(
            data.year, data.term, data.school_class, submission=submission,
            user=head_a, reason='Wrong score')
        marks_service.save_marks(
            assessment, [(student, '85', 'PRESENT')], teacher_a)
        marks_service.submit_for_review(
            data.year, data.term, data.school_class, data.subject, teacher_a)
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED

        marks_service.review_submission(submission, True, head_a, 'fixed')
        correction = MarkCorrection.objects.get(submission=submission)
        assert correction.status == MarkCorrection.Status.COMPLETED
        assert correction.completed_at is not None

    def test_published_reopen_unpublishes_and_unlocks(
            self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C5', student_count=1)
        student = data.students[0]
        assessment, submission = approve_sample(data, teacher_a, head_a)
        marks_service.publish_results(
            data.year, data.term, data.school_class, head_a)
        assert ResultPublication.is_locked(
            data.year, data.term, data.school_class)
        with pytest.raises(marks_service.MarkingError):
            marks_service.save_marks(
                assessment, [(student, '99', 'PRESENT')], teacher_a)

        marks_service.open_mark_correction(
            data.year, data.term, data.school_class, submission=submission,
            user=head_a, reason='Published error in the register')
        assert not ResultPublication.is_locked(
            data.year, data.term, data.school_class)
        publication = ResultPublication.objects.get(
            academic_year=data.year, term=data.term,
            school_class=data.school_class)
        assert publication.is_published is False

        marks_service.save_marks(
            assessment, [(student, '99', 'PRESENT')], teacher_a)

    def test_unpublish_requires_reason(self, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C6', student_count=1)
        approve_sample(data, teacher_a, head_a)
        marks_service.publish_results(
            data.year, data.term, data.school_class, head_a)
        with pytest.raises(marks_service.MarkingError):
            marks_service.unpublish_results(
                data.year, data.term, data.school_class, head_a, reason='  ')
        publication = ResultPublication.objects.get(
            academic_year=data.year, term=data.term,
            school_class=data.school_class)
        assert publication.is_published is True

        marks_service.unpublish_results(
            data.year, data.term, data.school_class, head_a, reason='Review')
        publication.refresh_from_db()
        assert publication.is_published is False


@pytest.mark.django_db
class TestCorrectionViews:
    def test_reopen_view_flow(self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'CV1', student_count=1)
        student = data.students[0]
        assessment, submission = approve_sample(data, teacher_a, head_a)
        client.force_login(head_a)

        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'reopen', 'reason': 'Score entered incorrectly'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.DRAFT

        correction = MarkCorrection.objects.get(submission=submission)
        assert correction.status == MarkCorrection.Status.OPEN
        assert correction.initiated_by_id == head_a.pk
        assert correction.reason

        # The teacher can now correct and resubmit; re-approval closes it.
        marks_service.save_marks(
            assessment, [(data.students[0], '88', 'PRESENT')], teacher_a)
        marks_service.submit_for_review(
            data.year, data.term, data.school_class, data.subject, teacher_a)
        marks_service.review_submission(
            MarkSubmission.objects.get(pk=submission.pk), True, head_a, 'ok')
        correction.refresh_from_db()
        assert correction.status == MarkCorrection.Status.COMPLETED

    def test_reopen_view_requires_reason(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C7', student_count=1)
        _assessment, submission = approve_sample(data, teacher_a, head_a)
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'reopen', 'reason': ''})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.APPROVED
        assert MarkCorrection.objects.count() == 0

    def test_reopen_view_rejects_unapproved(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C8', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(head_a)
        resp = client.post(
            reverse('academics:submission_review', args=[submission.pk]),
            {'action': 'reopen', 'reason': 'lets reopen'})
        assert resp.status_code == 302
        submission.refresh_from_db()
        assert submission.status == MarkSubmission.Status.SUBMITTED
        assert MarkCorrection.objects.count() == 0

    def test_teacher_cannot_open_review_view(
            self, client, school_a, teacher_a):
        data = build_school(school_a, 'C9', student_count=1)
        _assessment, submission = submit_sample(data, teacher_a)
        client.force_login(teacher_a)
        resp = client.get(
            reverse('academics:submission_review', args=[submission.pk]))
        assert resp.status_code == 302
        assert resp.url == reverse('dashboard')

    def test_results_unpublish_requires_reason(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'C10', student_count=1)
        approve_sample(data, teacher_a, head_a)
        marks_service.publish_results(
            data.year, data.term, data.school_class, head_a)
        client.force_login(head_a)
        url = reverse('academics:results')
        query = (f'?school_class={data.school_class.pk}'
                 f'&academic_year={data.year.pk}&term={data.term.pk}')

        # No reason -> still published.
        client.post(url + query, {
            'action': 'unpublish', 'reason': ''})
        publication = ResultPublication.objects.get(
            academic_year=data.year, term=data.term,
            school_class=data.school_class)
        assert publication.is_published is True

        client.post(url + query, {
            'action': 'unpublish', 'reason': 'Correction approved by the board'})
        publication.refresh_from_db()
        assert publication.is_published is False


# --------------------------------------------------------------------------- #
# Dashboard aggregates (real data, school scoped)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestDashboardStats:
    def test_head_dashboard_request_counts_are_scoped(
            self, client, school_a, school_b, head_a, head_b, teacher_a, teacher_b):
        build_school(school_a, 'DS1', student_count=1)
        build_school(school_b, 'DS2', student_count=1)
        make_request(school_a, head_a, teacher_a, title='A1')
        make_request(school_a, head_a, teacher_a, title='A2')
        theirs = make_request(school_b, head_b, teacher_b, title='B1')

        client.force_login(head_a)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.status_code == 200
        assert resp.context['open_request_count'] == 2
        ids = {r.pk for r in resp.context['recent_open_requests']}
        assert theirs.pk not in ids

        client.force_login(head_b)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.context['open_request_count'] == 1

    def test_head_dashboard_overdue_requests(
            self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'DS3', student_count=1)
        make_request(
            school_a, head_a, teacher_a,
            due_date=date.today() - timedelta(days=3))
        client.force_login(head_a)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.context['overdue_request_count'] == 1

    def test_teacher_dashboard_shows_only_own_open_requests(
            self, client, school_a, head_a, teacher_a, teacher_a2):
        build_school(school_a, 'DS4', student_count=1)
        mine = make_request(school_a, head_a, teacher_a, title='Mine')
        theirs = make_request(school_a, head_a, teacher_a2, title='Theirs')
        client.force_login(teacher_a)
        resp = client.get(reverse('academics:teacher_dashboard'))
        assert resp.status_code == 200
        assert resp.context['my_open_count'] == 1
        ids = {r.pk for r in resp.context['my_requests']}
        assert mine.pk in ids
        assert theirs.pk not in ids

    def test_head_dashboard_ready_to_publish_and_fee_summary(
            self, client, school_a, head_a, teacher_a):
        data = build_school(school_a, 'DS5', student_count=2, fees='100.00')
        assessment = build_assessment(data, 'Test', weight='100', max_score='100')
        marks_service.save_marks(assessment, [
            (data.students[0], '80', 'PRESENT'),
            (data.students[1], '60', 'PRESENT'),
        ], teacher_a)
        submission = marks_service.submit_for_review(
            data.year, data.term, data.school_class, data.subject, teacher_a)
        marks_service.review_submission(submission, True, head_a, 'ok')

        client.force_login(head_a)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.context['ready_to_publish'] == 1

        fee_summary = resp.context['fee_summary']
        assert fee_summary is not None
        assert fee_summary['charged'] == 200  # 2 students x 100.00
        assert fee_summary['outstanding'] == 200

        performance = resp.context['class_performance']
        averages = [row['average'] for row in performance if row['average'] is not None]
        assert averages and averages[0] == 70  # (80 + 60) / 2

        marks_service.publish_results(
            data.year, data.term, data.school_class, head_a)
        resp = client.get(reverse('academics:head_dashboard'))
        assert resp.context['ready_to_publish'] == 0

    def test_teacher_dashboard_open_count_and_tone(
            self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'DS5b', student_count=1)
        open_req = make_request(school_a, head_a, teacher_a, title='Open one')
        done_req = make_request(school_a, head_a, teacher_a, title='Done one')
        requests_service.transition(done_req, teacher_a, 'IN_PROGRESS')
        requests_service.transition(done_req, teacher_a, 'SUBMITTED', notes='x')
        requests_service.transition(done_req, head_a, 'COMPLETED')

        client.force_login(teacher_a)
        resp = client.get(reverse('academics:teacher_dashboard'))
        assert resp.context['my_open_count'] == 1
        assert resp.context['my_submitted_count'] == 0
        by_id = {r.pk: r for r in resp.context['my_requests']}
        assert by_id[open_req.pk].status_tone == 'neutral'
        assert by_id[done_req.pk].status_tone == 'success'

    def test_request_list_status_counts_are_scoped(
            self, client, school_a, head_a, teacher_a):
        build_school(school_a, 'DS6', student_count=1)
        make_request(school_a, head_a, teacher_a, title='P1')
        second = make_request(school_a, head_a, teacher_a, title='P2')
        requests_service.transition(second, teacher_a, 'IN_PROGRESS')
        requests_service.transition(second, teacher_a, 'SUBMITTED', notes='x')

        client.force_login(head_a)
        resp = client.get(reverse('academics:update_request_list'))
        assert resp.context['open_count'] == 1
        assert resp.context['submitted_count'] == 1

        resp = client.get(
            reverse('academics:update_request_list'), {'status': 'SUBMITTED'})
        titles = {r.title for r in resp.context['requests']}
        assert titles == {'P2'}
