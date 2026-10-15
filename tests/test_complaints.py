"""Teacher-raised complaints / requests / safeguarding tests."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import Complaint, ComplaintEvent
from apps.academics.services import complaints as complaint_service
from apps.academics.services import notifications as notifications_service
from apps.notifications.models import SMSNotification
from apps.schools.models import School

from tests.test_academics import build_school

User = get_user_model()


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
        username='cp_head_a', email='cha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a,
        phone='0244000002')


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='cp_head_b', email='chb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='cp_teacher_a', email='cta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


def _raise(teacher, school, **overrides):
    kwargs = {
        'school': school,
        'title': 'Broken desks in the classroom',
        'description': 'Several desks are broken and unsafe for the pupils.',
        'case_type': Complaint.CaseType.REQUEST,
        'category': Complaint.Category.FACILITIES,
    }
    kwargs.update(overrides)
    return complaint_service.create_complaint(teacher, **kwargs)


@pytest.mark.django_db
class TestComplaintService:
    def test_create_logs_event_and_audit(self, school_a, teacher_a):
        complaint = _raise(teacher_a, school_a)
        assert complaint.status == Complaint.Status.SUBMITTED
        assert complaint.school_id == school_a.pk
        actions = list(complaint.events.values_list('action', flat=True))
        assert ComplaintEvent.Action.CREATED in actions

    def test_safeguarding_is_escalated_immediately(self, school_a, teacher_a):
        complaint = _raise(
            teacher_a, school_a, case_type=Complaint.CaseType.SAFEGUARDING,
            category=Complaint.Category.SAFEGUARDING,
            supporting_details='Disclosed to the class teacher in confidence.')
        assert complaint.is_escalated is True
        assert ComplaintEvent.Action.ESCALATED in complaint.events.values_list(
            'action', flat=True)

    def test_urgent_priority_is_escalated(self, school_a, teacher_a):
        complaint = _raise(
            teacher_a, school_a, priority=Complaint.Priority.URGENT,
            supporting_details='Needs immediate attention.')
        assert complaint.is_escalated is True

    def test_lifecycle_and_resolution_notes(self, school_a, teacher_a, head_a):
        complaint = _raise(teacher_a, school_a)
        complaint_service.transition(
            complaint, head_a, Complaint.Status.ACKNOWLEDGED)
        complaint_service.transition(
            complaint, head_a, Complaint.Status.IN_PROGRESS)
        complaint_service.transition(
            complaint, head_a, Complaint.Status.RESOLVED,
            notes='Carpenter repaired the desks on Friday.')
        complaint.refresh_from_db()
        assert complaint.status == Complaint.Status.RESOLVED
        assert 'repaired the desks' in complaint.resolution_notes
        assert complaint.resolved_at is not None

    def test_raiser_can_reopen_resolved_case(self, school_a, teacher_a, head_a):
        complaint = _raise(teacher_a, school_a)
        complaint_service.transition(complaint, head_a, Complaint.Status.RESOLVED)
        complaint_service.transition(
            complaint, teacher_a, Complaint.Status.REOPENED)
        complaint.refresh_from_db()
        assert complaint.status == Complaint.Status.REOPENED
        assert complaint.resolved_at is None

    def test_teacher_cannot_resolve(self, school_a, teacher_a):
        complaint = _raise(teacher_a, school_a)
        with pytest.raises(complaint_service.ComplaintError):
            complaint_service.transition(
                complaint, teacher_a, Complaint.Status.RESOLVED)

    def test_assignment_rejects_cross_school_assignee(self, school_a, teacher_a,
                                                      head_a, head_b):
        complaint = _raise(teacher_a, school_a)
        # head_b belongs to another school, so the service refuses the assignment.
        with pytest.raises(complaint_service.ComplaintError):
            complaint_service.assign(complaint, head_a, head_b)
        complaint.refresh_from_db()
        assert complaint.assigned_to_id is None
        # A same-school assignee is accepted.
        complaint_service.assign(complaint, head_a, teacher_a)
        complaint.refresh_from_db()
        assert complaint.assigned_to_id == teacher_a.pk

    def test_comment_adds_history(self, school_a, teacher_a, head_a):
        complaint = _raise(teacher_a, school_a)
        complaint_service.add_comment(complaint, head_a, 'We will look into it.')
        assert ComplaintEvent.Action.COMMENTED in complaint.events.values_list(
            'action', flat=True)


@pytest.mark.django_db
class TestComplaintViews:
    def test_cross_school_detail_blocked(self, client, school_a, teacher_a, head_b):
        complaint = _raise(teacher_a, school_a)
        client.force_login(head_b)
        resp = client.get(reverse('academics:complaint_detail', args=[complaint.pk]))
        assert resp.status_code == 302

    def test_pages_render(self, client, school_a, teacher_a):
        build_school(school_a, 'CPV1')
        complaint = _raise(teacher_a, school_a)
        client.force_login(teacher_a)
        assert client.get(reverse('academics:complaint_list')).status_code == 200
        assert client.get(reverse('academics:complaint_create')).status_code == 200
        assert client.get(
            reverse('academics:complaint_detail', args=[complaint.pk])
        ).status_code == 200

    def test_teacher_can_raise_via_view(self, client, school_a, teacher_a):
        data = build_school(school_a, 'CPV2')
        client.force_login(teacher_a)
        resp = client.post(reverse('academics:complaint_create'), {
            'case_type': Complaint.CaseType.COMPLAINT,
            'category': Complaint.Category.ACADEMIC,
            'title': 'Not enough textbooks',
            'description': 'The class shares one textbook between four pupils.',
            'priority': Complaint.Priority.MEDIUM,
        })
        assert resp.status_code == 302
        assert Complaint.objects.filter(title='Not enough textbooks').exists()
        assert data.school_class.school_id == school_a.pk


@pytest.mark.django_db
class TestComplaintSms:
    def test_urgent_concern_sms_not_duplicated(self, school_a, teacher_a, head_a):
        complaint = _raise(
            teacher_a, school_a, case_type=Complaint.CaseType.SAFEGUARDING,
            category=Complaint.Category.SAFEGUARDING,
            supporting_details='Confidential disclosure.')
        notifications_service.notify_complaint_created(complaint)
        notifications_service.notify_complaint_created(complaint)
        key = f'acad:complaint-created:{complaint.pk}:{head_a.pk}'
        assert SMSNotification.objects.filter(unique_key=key).count() == 1
