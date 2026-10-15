"""Official terminal report lifecycle tests.

Covers generation (with incomplete-data gating and override), approval,
publication/supersession, return-for-correction, amendment, fee reconciliation
against the existing ledger, school isolation and the SMS side effects.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.academics.models import AcademicSettings, TerminalReport
from apps.academics.services import marks as marks_service
from apps.academics.services import notifications as notifications_service
from apps.academics.services import reports as reports_service
from apps.notifications.models import SMSNotification
from apps.school_fees.services import accounts as account_service
from apps.school_fees.services import payments as payment_service
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
        username='tr_admin', email='tra@test.com', password='x',
        first_name='Ada', last_name='Admin', role='SUPER_ADMIN', is_staff=True)


@pytest.fixture
def head_a(db, school_a):
    return User.objects.create_user(
        username='tr_head_a', email='trha@test.com', password='x',
        first_name='Head', last_name='A', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='tr_head_b', email='trhb@test.com', password='x',
        first_name='Head', last_name='B', role='HEAD_TEACHER', school=school_b)


@pytest.fixture
def teacher_a(db, school_a):
    return User.objects.create_user(
        username='tr_teacher_a', email='trta@test.com', password='x',
        first_name='Tee', last_name='A', role='TEACHER', school=school_a)


def _incomplete(data):
    """Create an assessment with no marks, leaving every result incomplete."""
    return build_assessment(data, 'Exam', weight='100', max_score='100')


def _complete(data, score='70'):
    """Enter a complete set of marks so the term results are not incomplete."""
    assessment = _incomplete(data)
    marks_service.save_marks(
        assessment, [(s, score, 'PRESENT') for s in data.students], None)
    return assessment


def _generate(data, student, user, **kwargs):
    return reports_service.generate_terminal_report(
        student, data.year, data.term, user, **kwargs)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestGeneration:
    def test_incomplete_results_block_generation(self, school_a, head_a):
        data = build_school(school_a, 'TG1', student_count=1)
        # An assessment with no marks leaves the subject result incomplete.
        _incomplete(data)
        with pytest.raises(reports_service.ReportError):
            _generate(data, data.students[0], head_a)
        assert TerminalReport.objects.count() == 0

    def test_force_without_reason_is_rejected(self, school_a, head_a):
        data = build_school(school_a, 'TG2', student_count=1)
        _incomplete(data)
        with pytest.raises(reports_service.ReportError):
            _generate(data, data.students[0], head_a, force=True)
        assert TerminalReport.objects.count() == 0

    def test_force_with_reason_generates_and_records_override(
            self, school_a, head_a):
        data = build_school(school_a, 'TG3', student_count=1)
        _incomplete(data)
        report = _generate(
            data, data.students[0], head_a, force=True,
            override_reason='Results being finalised.')
        assert report.status == TerminalReport.Status.GENERATED
        assert report.version == 1
        assert report.generated_by == head_a
        assert report.generated_at is not None
        assert 'finalised' in report.amendment_reason
        assert report.snapshot['student']['id'] == data.students[0].pk

    def test_complete_results_generate_without_override(self, school_a, head_a):
        data = build_school(school_a, 'TG4', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        assert report.status == TerminalReport.Status.GENERATED
        assert report.snapshot['complete'] is True
        assert report.average is not None
        assert report.version == 1

    def test_teacher_cannot_generate(self, school_a, teacher_a):
        data = build_school(school_a, 'TG5', student_count=1)
        _complete(data)
        with pytest.raises(reports_service.ReportError):
            _generate(data, data.students[0], teacher_a)

    def test_cross_school_user_cannot_generate(self, school_a, head_b):
        data = build_school(school_a, 'TG6', student_count=1)
        _complete(data)
        with pytest.raises(reports_service.ReportError):
            _generate(data, data.students[0], head_b)


# --------------------------------------------------------------------------- #
# Approval, publication and supersession
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestApprovalPublication:
    def test_only_approved_report_can_be_published(self, school_a, head_a):
        data = build_school(school_a, 'AP1', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        with pytest.raises(reports_service.ReportError):
            reports_service.publish_terminal_report(report, head_a)
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.GENERATED

    def test_approve_then_publish_makes_official(self, school_a, head_a):
        data = build_school(school_a, 'AP2', student_count=1)
        _complete(data)
        student = data.students[0]
        report = _generate(data, student, head_a)

        reports_service.approve_terminal_report(report, head_a, comment='Checked.')
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.APPROVED
        assert report.approved_by == head_a
        assert reports_service.official_report(student, data.year, data.term) is None

        reports_service.publish_terminal_report(report, head_a)
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.PUBLISHED
        assert report.published_by == head_a
        assert report.is_official is True
        official = reports_service.official_report(student, data.year, data.term)
        assert official is not None and official.pk == report.pk

    def test_cannot_approve_twice(self, school_a, head_a):
        data = build_school(school_a, 'AP3', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        reports_service.approve_terminal_report(report, head_a)
        report.refresh_from_db()
        with pytest.raises(reports_service.ReportError):
            reports_service.approve_terminal_report(report, head_a)

    def test_second_publish_supersedes_the_first(
            self, school_a, head_a):
        data = build_school(school_a, 'AP4', student_count=1)
        _complete(data)
        student = data.students[0]

        first = _generate(data, student, head_a)
        reports_service.approve_terminal_report(first, head_a)
        reports_service.publish_terminal_report(first, head_a)

        second = _generate(data, student, head_a)
        assert second.version == 2
        reports_service.approve_terminal_report(second, head_a)
        reports_service.publish_terminal_report(second, head_a)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == TerminalReport.Status.SUPERSEDED
        assert first.superseded_by_id == second.pk
        assert second.is_official is True
        assert reports_service.official_report(
            student, data.year, data.term).pk == second.pk
        assert reports_service.latest_report(
            student, data.year, data.term).pk == second.pk
        assert reports_service.report_history(
            student, data.year, data.term).count() == 2

    def test_publish_blocked_by_attendance_setting_then_override(
            self, school_a, head_a):
        data = build_school(school_a, 'AP5', student_count=1)
        _complete(data)
        settings_obj = AcademicSettings.for_school(school_a)
        settings_obj.require_attendance_for_publication = True
        settings_obj.save()

        student = data.students[0]
        report = _generate(
            data, student, head_a, force=True,
            override_reason='Attendance register not yet entered.')
        reports_service.approve_terminal_report(report, head_a)
        report.refresh_from_db()

        with pytest.raises(reports_service.ReportError):
            reports_service.publish_terminal_report(report, head_a)

        report.refresh_from_db()
        reports_service.publish_terminal_report(
            report, head_a, force=True, override_reason='Authorised by admin.')
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.PUBLISHED

    def test_cross_school_cannot_approve_or_publish(
            self, school_a, head_a, head_b):
        data = build_school(school_a, 'AP6', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        with pytest.raises(reports_service.ReportError):
            reports_service.approve_terminal_report(report, head_b)
        reports_service.approve_terminal_report(report, head_a)
        report.refresh_from_db()
        with pytest.raises(reports_service.ReportError):
            reports_service.publish_terminal_report(report, head_b)


# --------------------------------------------------------------------------- #
# Return for correction and amendment
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestReturnAndAmend:
    def test_return_requires_a_comment(self, school_a, head_a):
        data = build_school(school_a, 'RA1', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        with pytest.raises(reports_service.ReportError):
            reports_service.return_terminal_report(report, head_a, comment='  ')
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.GENERATED

    def test_return_sends_report_back_to_draft(self, school_a, head_a, admin_user):
        data = build_school(school_a, 'RA2', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], admin_user)
        reports_service.return_terminal_report(
            report, head_a, comment='Recheck the maths mark.')
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.DRAFT
        assert 'Recheck' in report.approval_comment

    def test_amend_supersedes_and_creates_new_version(self, school_a, head_a):
        data = build_school(school_a, 'RA3', student_count=1)
        _complete(data)
        student = data.students[0]
        report = _generate(data, student, head_a)
        reports_service.approve_terminal_report(report, head_a)
        reports_service.publish_terminal_report(report, head_a)

        replacement = reports_service.amend_terminal_report(
            report, head_a, reason='Corrected the final exam mark.')
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.SUPERSEDED
        assert report.superseded_by_id == replacement.pk
        assert report.amended_by == head_a
        assert report.amended_at is not None
        assert replacement.version == 2
        assert replacement.status == TerminalReport.Status.GENERATED
        # The old report is kept for the audit trail, not deleted.
        assert reports_service.report_history(
            student, data.year, data.term).count() == 2

    def test_amend_requires_reason(self, school_a, head_a):
        data = build_school(school_a, 'RA4', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        reports_service.approve_terminal_report(report, head_a)
        reports_service.publish_terminal_report(report, head_a)
        with pytest.raises(reports_service.ReportError):
            reports_service.amend_terminal_report(report, head_a, reason='')


# --------------------------------------------------------------------------- #
# Fee ledger integration
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestFeeIntegration:
    def test_report_snapshots_ledger_figures(self, school_a, head_a, admin_user):
        data = build_school(school_a, 'FI1', student_count=1, fees='250.00')
        _complete(data)
        student = data.students[0]
        account = account_service.get_or_create_fee_account(student, data.term)
        payment_service.record_payment(
            account, Decimal('100.00'), recorded_by=admin_user)
        account.refresh_from_db()

        report = _generate(data, student, head_a)
        assert report.total_fees == Decimal('250.00')
        assert report.amount_paid == Decimal('100.00')
        assert report.outstanding == Decimal('150.00')
        assert report.arrears == Decimal('0.00')

        snapshot_finance = report.snapshot['finance']
        assert snapshot_finance['has_account'] is True
        assert snapshot_finance['total_fees'] == '250.00'
        assert snapshot_finance['amount_paid'] == '100.00'
        assert snapshot_finance['outstanding'] == '150.00'

    def test_figures_reconcile_with_the_existing_ledger(self, school_a):
        data = build_school(school_a, 'FI2', student_count=1, fees='300.00')
        student = data.students[0]
        account_service.get_or_create_fee_account(student, data.term)
        finance = reports_service.financial_summary(
            student, data.year, data.term)
        # Never a second ledger: the figures match StudentFeeAccount exactly.
        account = student.fee_accounts.get(term=data.term)
        assert finance['total_fees'] == account.total_fees
        assert finance['amount_paid'] == account.amount_paid


# --------------------------------------------------------------------------- #
# SMS side effects
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestPublishSms:
    def test_parent_sms_sent_once_on_publication(self, school_a, head_a):
        data = build_school(school_a, 'SM1', student_count=1)
        _complete(data)
        student = data.students[0]
        report = _generate(data, student, head_a)
        reports_service.approve_terminal_report(report, head_a)
        reports_service.publish_terminal_report(report, head_a)

        key = f'acad:report-published:{report.pk}'
        assert SMSNotification.objects.filter(unique_key=key).count() == 1

        # Retrying the notification must not duplicate the message.
        notifications_service.notify_terminal_report_published(report)
        assert SMSNotification.objects.filter(unique_key=key).count() == 1


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestTerminalReportViews:
    def test_hub_renders(self, client, school_a, head_a):
        build_school(school_a, 'TV1', student_count=1)
        client.force_login(head_a)
        resp = client.get(reverse('academics:terminal_report_hub'))
        assert resp.status_code == 200

    def test_generate_via_student_page_then_review(
            self, client, school_a, head_a):
        data = build_school(school_a, 'TV2', student_count=1)
        _complete(data)
        student = data.students[0]
        client.force_login(head_a)

        resp = client.post(
            reverse('academics:terminal_report', args=[student.pk]),
            {'action': 'generate'})
        assert resp.status_code == 302
        report = TerminalReport.objects.get(student=student)
        assert resp.url == reverse(
            'academics:terminal_report_review', args=[report.pk])

        resp = client.get(
            reverse('academics:terminal_report_review', args=[report.pk]))
        assert resp.status_code == 200

    def test_review_view_approve_and_publish(
            self, client, school_a, head_a):
        data = build_school(school_a, 'TV3', student_count=1)
        _complete(data)
        student = data.students[0]
        report = _generate(data, student, head_a)
        client.force_login(head_a)

        url = reverse('academics:terminal_report_review', args=[report.pk])
        resp = client.post(url, {'action': 'approve', 'approve-comment': 'Ok'})
        assert resp.status_code == 302
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.APPROVED

        resp = client.post(url, {'action': 'publish'})
        assert resp.status_code == 302
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.PUBLISHED

    def test_cross_school_review_is_blocked(self, client, school_a, head_a, head_b):
        data = build_school(school_a, 'TV4', student_count=1)
        _complete(data)
        report = _generate(data, data.students[0], head_a)
        client.force_login(head_b)
        resp = client.get(
            reverse('academics:terminal_report_review', args=[report.pk]))
        assert resp.status_code == 302
        report.refresh_from_db()
        assert report.status == TerminalReport.Status.GENERATED

    def test_bulk_generation_for_a_class(self, client, school_a, head_a):
        data = build_school(school_a, 'TV5', student_count=2)
        _complete(data)
        client.force_login(head_a)
        resp = client.post(reverse('academics:terminal_report_bulk'), {
            'school_class': data.school_class.pk,
        })
        assert resp.status_code == 302
        assert TerminalReport.objects.filter(
            school=school_a, status=TerminalReport.Status.GENERATED).count() == 2
