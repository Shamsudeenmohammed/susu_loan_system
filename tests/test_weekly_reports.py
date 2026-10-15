"""Weekly teacher report tests: uniqueness, workflow, isolation and SMS."""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import WeeklyReport, WeeklyReportEvent
from apps.academics.services import notifications as notifications_service
from apps.academics.services import weekly_reports as weekly_service
from apps.notifications.models import SMSNotification
from apps.schools.models import School

from tests.test_academics import build_school

User = get_user_model()

WEEK = date(2026, 2, 2)  # a Monday


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
        username='wr_head_a', email='wha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a,
        phone='0244000001')


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='wr_head_b', email='whb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='wr_teacher_a', email='wta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


@pytest.mark.django_db
class TestWeeklyReportService:
    def _draft(self, data, teacher, summary='Covered fractions.'):
        return weekly_service.save_draft(
            actor=teacher, teacher=teacher, school=data.school,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary=summary)

    def test_same_week_updated_in_place_not_duplicated(self, school_a, teacher_a):
        data = build_school(school_a, 'WR1')
        first = self._draft(data, teacher_a, 'First summary.')
        second = self._draft(data, teacher_a, 'Revised summary.')
        assert first.pk == second.pk
        assert WeeklyReport.objects.count() == 1
        second.refresh_from_db()
        assert second.summary == 'Revised summary.'

    def test_submitted_week_cannot_be_refiled(self, school_a, teacher_a):
        data = build_school(school_a, 'WR2')
        report = self._draft(data, teacher_a)
        weekly_service.submit(report, teacher_a)
        report.refresh_from_db()
        assert report.status == WeeklyReport.Status.SUBMITTED
        with pytest.raises(weekly_service.WeeklyReportError):
            self._draft(data, teacher_a, 'Trying again for the same week.')

    def test_only_owner_may_submit(self, school_a, teacher_a, head_a):
        data = build_school(school_a, 'WR3')
        report = self._draft(data, teacher_a)
        with pytest.raises(weekly_service.WeeklyReportError):
            weekly_service.submit(report, head_a)

    def test_teacher_cannot_review(self, school_a, teacher_a):
        data = build_school(school_a, 'WR4')
        report = self._draft(data, teacher_a)
        weekly_service.submit(report, teacher_a)
        report.refresh_from_db()
        with pytest.raises(weekly_service.WeeklyReportError):
            weekly_service.review(
                report, teacher_a, WeeklyReport.Status.REVIEWED)

    def test_clarification_reopens_same_row(self, school_a, teacher_a, head_a):
        data = build_school(school_a, 'WR5')
        report = self._draft(data, teacher_a, 'Needs work.')
        weekly_service.submit(report, teacher_a)
        report.refresh_from_db()

        weekly_service.review(
            report, head_a, WeeklyReport.Status.CLARIFICATION_REQUESTED,
            comment='Please add the challenges you faced.')
        report.refresh_from_db()
        assert report.status == WeeklyReport.Status.CLARIFICATION_REQUESTED
        assert weekly_service.can_edit(teacher_a, report) is True

        revised = self._draft(data, teacher_a, 'Updated with challenges.')
        assert revised.pk == report.pk
        assert WeeklyReport.objects.count() == 1

        weekly_service.submit(revised, teacher_a)
        revised.refresh_from_db()
        weekly_service.review(revised, head_a, WeeklyReport.Status.REVIEWED)
        revised.refresh_from_db()
        assert revised.status == WeeklyReport.Status.REVIEWED
        # Every step is preserved in the history.
        actions = list(revised.events.values_list('action', flat=True))
        assert WeeklyReportEvent.Action.SUBMITTED in actions
        assert WeeklyReportEvent.Action.CLARIFICATION_REQUESTED in actions

    def test_events_recorded_for_draft(self, school_a, teacher_a):
        data = build_school(school_a, 'WR6')
        report = self._draft(data, teacher_a)
        assert report.events.count() == 1
        self._draft(data, teacher_a, 'Updated.')
        assert report.events.count() == 2


@pytest.mark.django_db
class TestWeeklyReportViews:
    def test_teacher_list_shows_only_own_reports(self, client, school_a, teacher_a):
        data = build_school(school_a, 'WRV1')
        other = User.objects.create_user(
            username='wr_other', email='o@test.com', password='x',
            first_name='Other', last_name='T', role='TEACHER', school=school_a)
        weekly_service.save_draft(
            actor=teacher_a, teacher=teacher_a, school=school_a,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary='Mine.')
        weekly_service.save_draft(
            actor=other, teacher=other, school=school_a,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary='Theirs.')

        client.force_login(teacher_a)
        resp = client.get(reverse('academics:weekly_report_list'))
        assert resp.status_code == 200
        reports = list(resp.context['reports'])
        assert reports and all(r.teacher_id == teacher_a.pk for r in reports)

    def test_cross_school_detail_is_blocked(self, client, school_a, school_b,
                                            teacher_a, head_b):
        data = build_school(school_a, 'WRV2')
        report = weekly_service.save_draft(
            actor=teacher_a, teacher=teacher_a, school=school_a,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary='Secret.')
        client.force_login(head_b)
        resp = client.get(
            reverse('academics:weekly_report_detail', args=[report.pk]))
        assert resp.status_code == 302
        assert not weekly_service.can_view(head_b, report)

    def test_pages_render(self, client, school_a, teacher_a):
        data = build_school(school_a, 'WRV3')
        weekly_service.save_draft(
            actor=teacher_a, teacher=teacher_a, school=school_a,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary='Hello.')
        client.force_login(teacher_a)
        assert client.get(
            reverse('academics:weekly_report_list')).status_code == 200
        assert client.get(
            reverse('academics:weekly_report_create')).status_code == 200
        report = WeeklyReport.objects.get()
        assert client.get(
            reverse('academics:weekly_report_detail', args=[report.pk])
        ).status_code == 200

    def test_admin_can_create_via_view(self, client, school_a, teacher_a, admin_user):
        data = build_school(school_a, 'WRV4')
        client.force_login(admin_user)
        resp = client.post(reverse('academics:weekly_report_create'), {
            'school': school_a.pk,
            'teacher': teacher_a.pk,
            'school_class': data.school_class.pk,
            'academic_year': data.year.pk,
            'term': data.term.pk,
            'week_start': WEEK.isoformat(),
            'summary': 'Created by an administrator on behalf of the teacher.',
        })
        assert resp.status_code == 302
        report = WeeklyReport.objects.get()
        assert report.teacher_id == teacher_a.pk
        assert report.status == WeeklyReport.Status.DRAFT


@pytest.mark.django_db
class TestWeeklyReportSms:
    def test_sms_is_not_duplicated(self, school_a, teacher_a, head_a):
        data = build_school(school_a, 'WRS1')
        report = weekly_service.save_draft(
            actor=teacher_a, teacher=teacher_a, school=school_a,
            school_class=data.school_class, academic_year=data.year,
            term=data.term, week_start=WEEK, summary='Weekly.')
        weekly_service.submit(report, teacher_a)
        report.refresh_from_db()

        # Submitting again (or a retried notification) must not duplicate the SMS.
        notifications_service.notify_weekly_report_submitted(report)
        notifications_service.notify_weekly_report_submitted(report)
        key = f'acad:weekly-submitted:{report.pk}:{head_a.pk}'
        assert SMSNotification.objects.filter(unique_key=key).count() == 1
