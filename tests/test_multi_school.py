"""Multi-school isolation, scoping, dashboard and command tests."""

import pytest
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.schools.models import School
from apps.schools.scoping import (
    SESSION_KEY,
    can_access_school,
    get_active_school,
    is_global_user,
    scope_queryset,
)
from apps.school_fees.models import (
    AcademicYear,
    FeeCategory,
    FeePayment,
    FeeStructure,
    ReminderLog,
    SchoolClass,
    Student,
    StudentFeeAccount,
    Term,
)
from apps.school_fees.forms import (
    AssignFeeForm,
    FeeStructureForm,
    SchoolClassForm,
    StudentForm,
)
from apps.school_fees.services import accounts as account_service
from apps.school_fees.services import payments as payment_service
from apps.school_fees.school_links import backfill_school_from_ownership

User = get_user_model()

MANKARIGU = 'MANKARIGU'
LINGBINSI = 'LINGBINSI'


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def school_a(db):
    return School.objects.create(name='School Alpha', code='ALPHA', short_name='Alpha')


@pytest.fixture
def school_b(db):
    return School.objects.create(name='School Beta', code='BETA', short_name='Beta')


@pytest.fixture
def proprietor(db):
    return User.objects.create_user(
        username='proprietor', email='p@test.com', password='x',
        first_name='Pro', last_name='Prietor', role='PROPRIETOR')


@pytest.fixture
def head_a(db, school_a):
    return User.objects.create_user(
        username='head_alpha', email='ha@test.com', password='x',
        first_name='Head', last_name='Alpha', role='HEAD_TEACHER', school=school_a)


@pytest.fixture
def head_b(db, school_b):
    return User.objects.create_user(
        username='head_beta', email='hb@test.com', password='x',
        first_name='Head', last_name='Beta', role='HEAD_TEACHER', school=school_b)


class DummyRequest:
    def __init__(self, user, session=None):
        self.user = user
        self.session = session if session is not None else {}


def build_school(school, suffix, amount='100.00'):
    """Create a full set of school-owned records for one branch."""
    school_class = SchoolClass.objects.create(
        school=school, name=f'Class {suffix}', code=f'C-{suffix}')
    year = AcademicYear.objects.create(school=school, name=f'2025/2026 {suffix}', is_active=True)
    term = Term.objects.create(academic_year=year, name='First Term', term_number=1)
    category = FeeCategory.objects.create(
        school=school, name=f'Tuition {suffix}', code=f'TU-{suffix}')
    student = Student.objects.create(
        school=school, first_name='Pupil', last_name=suffix, school_class=school_class,
        parent_name='Parent', parent_phone='0240000000')
    FeeStructure.objects.create(
        academic_year=year, term=term, school_class=school_class,
        fee_category=category, amount=Decimal(amount),
        due_date=timezone.now().date())
    account = account_service.get_or_create_fee_account(student, term)
    return SimpleNamespace(
        school=school, school_class=school_class, year=year, term=term,
        category=category, student=student, account=account)


# --------------------------------------------------------------------------- #
# Model derivation + per-school uniqueness
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestSchoolOwnership:
    def test_student_inherits_school_from_class(self, school_a):
        data = build_school(school_a, 'A')
        assert data.student.school_id == school_a.pk

    def test_fee_structure_inherits_school_from_year(self, school_a):
        data = build_school(school_a, 'A2')
        fs = FeeStructure.objects.get(school_class=data.school_class)
        assert fs.school_id == school_a.pk

    def test_fee_account_and_payment_inherit_school(self, school_a):
        data = build_school(school_a, 'A3')
        payment = payment_service.record_payment(
            data.account, Decimal('40.00'), recorded_by=None)
        assert data.account.school_id == school_a.pk
        assert payment.school_id == school_a.pk

    def test_reminder_log_inherits_school(self, school_a):
        data = build_school(school_a, 'A4')
        log = ReminderLog.objects.create(
            student=data.student, parent_phone='0240000000', message='hi')
        assert log.school_id == school_a.pk

    def test_same_class_code_allowed_across_schools(self, school_a, school_b):
        SchoolClass.objects.create(school=school_a, name='Class 1', code='C1')
        SchoolClass.objects.create(school=school_b, name='Class 1', code='C1')
        assert SchoolClass.objects.filter(code='C1').count() == 2

    def test_duplicate_class_code_same_school_rejected(self, school_a):
        SchoolClass.objects.create(school=school_a, name='Class 1', code='C1')
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                SchoolClass.objects.create(school=school_a, name='Class 1b', code='C1')


# --------------------------------------------------------------------------- #
# Scoping helpers
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestScopingHelpers:
    def test_global_user_detection(self, proprietor, head_a):
        assert is_global_user(proprietor) is True
        assert is_global_user(head_a) is False

    def test_can_access_school(self, head_a, school_a, school_b):
        assert can_access_school(head_a, school_a) is True
        assert can_access_school(head_a, school_b) is False

    def test_head_teacher_sees_only_own_school(self, school_a, school_b, head_a):
        build_school(school_a, 'HA')
        build_school(school_b, 'HB')
        request = DummyRequest(head_a)
        assert scope_queryset(Student.objects.all(), request).count() == 1
        assert scope_queryset(Student.objects.all(), request).first().school_id == school_a.pk

    def test_proprietor_sees_all_schools(self, school_a, school_b, proprietor):
        build_school(school_a, 'PA')
        build_school(school_b, 'PB')
        request = DummyRequest(proprietor)
        assert scope_queryset(Student.objects.all(), request).count() == 2

    def test_proprietor_switch_narrows_scope(self, school_a, school_b, proprietor):
        build_school(school_a, 'SW_A')
        build_school(school_b, 'SW_B')
        request = DummyRequest(proprietor, {SESSION_KEY: school_a.pk})
        assert get_active_school(request) == school_a
        students = scope_queryset(Student.objects.all(), request)
        assert students.count() == 1
        assert students.first().school_id == school_a.pk

    def test_can_access_school_helpers(self, head_a, school_a, school_b):
        assert can_access_school(head_a, school_a) is True
        assert can_access_school(head_a, school_b) is False
        assert can_access_school(head_a, None) is False


# --------------------------------------------------------------------------- #
# View-level isolation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestViewIsolation:
    def test_head_sees_only_own_students(self, client, head_a, school_a, school_b):
        build_school(school_a, 'VA')
        b = build_school(school_b, 'VB')
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_student_list'))
        assert resp.status_code == 200
        students = list(resp.context['students'])
        assert all(s.school_id == school_a.pk for s in students)
        assert b.student not in students

    def test_head_cannot_open_other_student(self, client, head_a, school_b):
        b = build_school(school_b, 'VD')
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_student_detail', args=[b.student.pk]))
        assert resp.status_code == 404

    def test_head_cannot_open_other_account(self, client, head_a, school_b):
        b = build_school(school_b, 'VE')
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_fee_account_detail', args=[b.account.pk]))
        assert resp.status_code == 404

    def test_head_cannot_open_other_receipt(self, client, head_a, school_b):
        b = build_school(school_b, 'VF')
        payment = payment_service.record_payment(b.account, Decimal('10.00'))
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_receipt', args=[payment.pk]))
        assert resp.status_code == 404

    def test_proprietor_sees_every_student(self, client, proprietor, school_a, school_b):
        build_school(school_a, 'VG')
        build_school(school_b, 'VH')
        client.force_login(proprietor)
        resp = client.get(reverse('school_fees_student_list'))
        assert resp.status_code == 200
        assert len(list(resp.context['students'])) == 2

    def test_proprietor_switch_narrows_views(self, client, proprietor, school_a, school_b):
        build_school(school_a, 'VI')
        build_school(school_b, 'VJ')
        client.force_login(proprietor)
        resp = client.post(reverse('switch_school'),
                           {'school': school_a.pk, 'next': reverse('school_fees_student_list')})
        assert resp.status_code == 302
        resp = client.get(reverse('school_fees_student_list'))
        students = list(resp.context['students'])
        assert len(students) == 1
        assert students[0].school_id == school_a.pk

    def test_head_teacher_cannot_switch_school(self, client, head_a, school_b):
        client.force_login(head_a)
        resp = client.post(reverse('switch_school'), {'school': school_b.pk})
        assert resp.status_code == 302
        assert client.session.get(SESSION_KEY) is None

    def test_payment_list_is_school_scoped(self, client, head_a, school_a, school_b):
        a = build_school(school_a, 'VK')
        b = build_school(school_b, 'VL')
        p_a = payment_service.record_payment(a.account, Decimal('15.00'))
        payment_service.record_payment(b.account, Decimal('25.00'))
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_payment_list'))
        payments = list(resp.context['payments'])
        assert [p.pk for p in payments] == [p_a.pk]
        assert all(p.school_id == school_a.pk for p in payments)

    def test_export_csv_includes_school_and_is_scoped(self, client, head_a, school_a, school_b):
        a = build_school(school_a, 'VM')
        b = build_school(school_b, 'VN')
        payment_service.record_payment(a.account, Decimal('11.00'))
        payment_service.record_payment(b.account, Decimal('22.00'))
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_export_csv'), {'type': 'payments'})
        assert resp.status_code == 200
        body = resp.content.decode('utf-8')
        header = body.splitlines()[0]
        assert 'School' in header
        assert school_a.name in body
        assert school_b.name not in body


# --------------------------------------------------------------------------- #
# Dashboard routing
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestDashboardRouting:
    def test_head_teacher_redirected_to_fee_dashboard(self, client, head_a):
        client.force_login(head_a)
        resp = client.get(reverse('dashboard'))
        assert resp.status_code == 302
        assert resp.url == reverse('school_fees_dashboard')

    def test_proprietor_gets_proprietor_dashboard(self, client, proprietor, school_a, school_b):
        build_school(school_a, 'DA')
        build_school(school_b, 'DB')
        client.force_login(proprietor)
        resp = client.get(reverse('dashboard'))
        assert resp.status_code == 200
        assert 'dashboard/proprietor_dashboard.html' in [t.name for t in resp.templates]
        pks = {row['school'].pk for row in resp.context['per_school']}
        assert {school_a.pk, school_b.pk} <= pks
        assert resp.context['combined_students'] == 2

    def test_proprietor_dashboard_scoped_to_switched_school(self, client, proprietor, school_a, school_b):
        build_school(school_a, 'DC')
        build_school(school_b, 'DD')
        client.force_login(proprietor)
        client.post(reverse('switch_school'), {'school': school_a.pk})
        resp = client.get(reverse('dashboard'))
        assert resp.status_code == 200
        assert len(resp.context['per_school']) == 1
        assert resp.context['per_school'][0]['school'].pk == school_a.pk


# --------------------------------------------------------------------------- #
# Management commands
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestCommands:
    def test_setup_schools_creates_both_idempotently(self):
        call_command('setup_schools', verbosity=0)
        call_command('setup_schools', verbosity=0)
        assert School.objects.filter(code=MANKARIGU).count() == 1
        assert School.objects.filter(code=LINGBINSI).count() == 1

    def test_setup_schools_with_users(self):
        call_command(
            'setup_schools', '--with-users',
            proprietor_password='pw-pro', mankarigu_password='pw-a',
            lingbinsi_password='pw-b', verbosity=0)
        proprietor = User.objects.get(username='proprietor')
        assert proprietor.role == 'PROPRIETOR'
        assert proprietor.school_id is None
        mankari = User.objects.get(username='mankarigu_head')
        lingbi = User.objects.get(username='lingbinsi_head')
        assert mankari.role == 'HEAD_TEACHER'
        assert mankari.school.code == MANKARIGU
        assert lingbi.school.code == LINGBINSI
        assert mankari.check_password('pw-a')
        assert lingbi.check_password('pw-b')

    def test_assign_school_data_moves_legacy_rows(self):
        call_command('setup_schools', verbosity=0)
        school = School.objects.get(code=MANKARIGU)
        legacy_class = SchoolClass.objects.create(name='Legacy', code='LEG')
        legacy_student = Student.objects.create(
            first_name='Old', last_name='Pupil', school_class=legacy_class,
            parent_name='P', parent_phone='0240000000')
        assert legacy_student.school_id is None
        call_command('assign_school_data', MANKARIGU, verbosity=0)
        legacy_class.refresh_from_db()
        legacy_student.refresh_from_db()
        assert legacy_class.school_id == school.pk
        assert legacy_student.school_id == school.pk

    def test_assign_school_data_dry_run(self):
        call_command('setup_schools', verbosity=0)
        legacy_class = SchoolClass.objects.create(name='Legacy2', code='LEG2')
        call_command('assign_school_data', MANKARIGU, '--dry-run', verbosity=0)
        legacy_class.refresh_from_db()
        assert legacy_class.school_id is None


# --------------------------------------------------------------------------- #
# Registration: explicit school choice + server-side validation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestRegistrationSchoolEnforcement:
    def test_student_form_requires_school_when_combined(self, school_a):
        data = build_school(school_a, 'RA')
        form = StudentForm({
            'first_name': 'New', 'last_name': 'Kid',
            'school_class': data.school_class.pk,
            'parent_name': 'Parent', 'parent_phone': '0241111111',
            'parent_email': '', 'is_active': 'on',
        })
        assert not form.is_valid()
        assert 'school' in form.errors

    def test_student_form_accepts_explicit_school(self, school_a):
        data = build_school(school_a, 'RB')
        form = StudentForm({
            'school': school_a.pk, 'first_name': 'New', 'last_name': 'Kid',
            'school_class': data.school_class.pk,
            'parent_name': 'Parent', 'parent_phone': '0241111111',
            'parent_email': '', 'is_active': 'on',
        })
        assert form.is_valid(), form.errors
        assert form.save().school_id == school_a.pk

    def test_student_form_rejects_class_from_other_school(self, school_a, school_b):
        build_school(school_a, 'RC')
        b = build_school(school_b, 'RD')
        form = StudentForm({
            'school': school_a.pk, 'first_name': 'New', 'last_name': 'Kid',
            'school_class': b.school_class.pk,
            'parent_name': 'Parent', 'parent_phone': '0241111111',
            'parent_email': '', 'is_active': 'on',
        })
        assert not form.is_valid()
        assert 'school_class' in form.errors

    def test_student_form_pins_school_for_scoped_user(self, school_a, school_b):
        b = build_school(school_b, 'RE')
        form = StudentForm(
            {'first_name': 'New', 'last_name': 'Kid',
             'school_class': b.school_class.pk,
             'parent_name': 'Parent', 'parent_phone': '0241111111',
             'parent_email': '', 'is_active': 'on', 'school': school_a.pk},
            school=school_a)
        assert 'school' not in form.fields
        assert form.instance.school_id == school_a.pk
        assert not form.is_valid()
        assert 'school_class' in form.errors

    def test_legacy_class_is_selectable_and_adopted(self, school_a):
        """A school-less (legacy) class must appear for a global admin and be
        claimed by the chosen branch when the student is saved."""
        legacy_class = SchoolClass.objects.create(name='Legacy P1', code='LP1')
        form = StudentForm({
            'school': school_a.pk, 'first_name': 'New', 'last_name': 'Kid',
            'school_class': legacy_class.pk,
            'parent_name': 'Parent', 'parent_phone': '0241111111',
            'parent_email': '', 'is_active': 'on',
        })
        assert legacy_class in form.fields['school_class'].queryset
        assert form.is_valid(), form.errors
        student = form.save()
        student.refresh_from_db()
        legacy_class.refresh_from_db()
        assert student.school_id == school_a.pk
        assert legacy_class.school_id == school_a.pk

    def test_scoped_user_cannot_see_legacy_class(self, school_a):
        """A branch user must never see another branch's/unassigned classes."""
        SchoolClass.objects.create(name='Legacy P2', code='LP2')
        form = StudentForm(school=school_a)
        codes = list(form.fields['school_class'].queryset.values_list('code', flat=True))
        assert 'LP2' not in codes

    def test_class_form_requires_school_when_combined(self):
        form = SchoolClassForm({'name': 'New Class', 'code': 'NC', 'is_active': 'on'})
        assert not form.is_valid()
        assert 'school' in form.errors

    def test_class_form_pins_school_for_scoped_user(self, school_a):
        form = SchoolClassForm(
            {'name': 'Pinned', 'code': 'PIN', 'is_active': 'on', 'school': ''},
            school=school_a)
        assert 'school' not in form.fields
        assert form.is_valid(), form.errors
        assert form.save(commit=False).school_id == school_a.pk

    def test_fee_structure_form_requires_school_when_combined(self, school_a):
        data = build_school(school_a, 'RF')
        form = FeeStructureForm({
            'academic_year': data.year.pk, 'term': data.term.pk,
            'school_class': data.school_class.pk, 'fee_category': data.category.pk,
            'amount': '50.00', 'due_date': '2026-01-31', 'is_active': 'on'})
        assert not form.is_valid()
        assert 'school' in form.errors

    def test_assign_fee_form_requires_school_when_combined(self, school_a):
        data = build_school(school_a, 'RG')
        form = AssignFeeForm({
            'scope': 'all', 'academic_year': data.year.pk, 'term': data.term.pk,
            'fee_category': data.category.pk, 'amount': '50.00',
            'due_date': '2026-01-31'})
        assert not form.is_valid()
        assert 'school' in form.errors


# --------------------------------------------------------------------------- #
# Cross-school writes are blocked server-side
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestCrossSchoolWrites:
    def test_head_cannot_create_student_in_other_school(
            self, client, head_a, school_a, school_b):
        build_school(school_a, 'CA')
        b = build_school(school_b, 'CB')
        client.force_login(head_a)
        resp = client.post(reverse('school_fees_student_create'), {
            'first_name': 'Sneaky', 'last_name': 'Pupil',
            'school_class': b.school_class.pk,  # manipulated: other school
            'parent_name': 'P', 'parent_phone': '0240000000',
            'parent_email': '', 'is_active': 'on',
        })
        assert resp.status_code == 200
        assert not Student.objects.filter(first_name='Sneaky').exists()
        assert Student.objects.filter(school=school_b).count() == 1

    def test_head_cannot_update_other_school_student(self, client, head_a, school_b):
        b = build_school(school_b, 'CU')
        client.force_login(head_a)
        resp = client.post(reverse('school_fees_student_update', args=[b.student.pk]), {
            'first_name': 'Hacked', 'last_name': 'X',
            'school_class': b.school_class.pk,
            'parent_name': 'P', 'parent_phone': '0240000000',
            'parent_email': '', 'is_active': 'on',
        })
        assert resp.status_code == 404
        b.student.refresh_from_db()
        assert b.student.first_name != 'Hacked'

    def test_head_class_create_ignores_posted_school(
            self, client, head_a, school_a, school_b):
        client.force_login(head_a)
        resp = client.post(reverse('school_fees_class_create'), {
            'school': school_b.pk,  # manipulated: must be ignored
            'name': 'Pinned Class', 'code': 'PC', 'is_active': 'on'})
        assert resp.status_code == 302
        assert SchoolClass.objects.get(code='PC').school_id == school_a.pk

    def test_head_assign_fee_all_stays_in_own_school(
            self, client, head_a, school_a, school_b):
        a = build_school(school_a, 'AFA')
        b = build_school(school_b, 'AFB')
        # Remove the auto-created account so any cross-school leak is obvious.
        StudentFeeAccount.objects.filter(student=b.student).delete()
        client.force_login(head_a)
        resp = client.post(reverse('school_fees_assign_fee'), {
            'school': school_b.pk,  # manipulated: must be ignored
            'scope': 'all', 'academic_year': a.year.pk, 'term': a.term.pk,
            'fee_category': a.category.pk, 'amount': '75.00',
            'due_date': '2026-01-31'})
        assert resp.status_code in (200, 302)
        assert StudentFeeAccount.objects.filter(student=b.student).count() == 0
        assert StudentFeeAccount.objects.filter(student=a.student).count() == 1

    def test_global_admin_creates_in_explicitly_chosen_school(
            self, client, admin_user, school_a, school_b):
        build_school(school_a, 'CG')
        b = build_school(school_b, 'CH')
        client.force_login(admin_user)
        resp = client.post(reverse('school_fees_student_create'), {
            'school': school_b.pk, 'first_name': 'Fresh', 'last_name': 'Pupil',
            'school_class': b.school_class.pk, 'parent_name': 'P',
            'parent_phone': '0240000000', 'parent_email': '', 'is_active': 'on',
        })
        assert resp.status_code == 302
        assert Student.objects.get(first_name='Fresh').school_id == school_b.pk

    def test_global_class_create_requires_school(self, client, admin_user, school_a):
        client.force_login(admin_user)
        resp = client.post(reverse('school_fees_class_create'),
                           {'name': 'No School', 'code': 'NS', 'is_active': 'on'})
        assert resp.status_code == 200
        assert not SchoolClass.objects.filter(code='NS').exists()
        resp = client.post(reverse('school_fees_class_create'), {
            'school': school_a.pk, 'name': 'Has School', 'code': 'HS',
            'is_active': 'on'})
        assert resp.status_code == 302
        assert SchoolClass.objects.get(code='HS').school_id == school_a.pk


# --------------------------------------------------------------------------- #
# Search / autocomplete isolation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestSearchIsolation:
    def test_student_search_is_scoped(self, client, head_a, school_a, school_b):
        build_school(school_a, 'SSA')
        b = build_school(school_b, 'SSB')
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_student_list'),
                          {'q': b.student.student_id})
        assert list(resp.context['students']) == []

    def test_student_autocomplete_is_scoped(self, client, head_a, school_a, school_b):
        a = build_school(school_a, 'ACA')
        b = build_school(school_b, 'ACB')
        client.force_login(head_a)
        resp = client.get(reverse('school_fees_student_autocomplete'),
                          {'q': b.student.student_id})
        assert resp.json()['results'] == []
        resp = client.get(reverse('school_fees_student_autocomplete'),
                          {'q': a.student.student_id})
        assert {r['id'] for r in resp.json()['results']} == {a.student.pk}


# --------------------------------------------------------------------------- #
# Proprietor dashboard metrics
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestProprietorMetrics:
    def test_staff_and_teacher_counts(self, client, proprietor, school_a, school_b):
        build_school(school_a, 'DM')
        build_school(school_b, 'DN')
        User.objects.create_user(username='head_x', password='x',
                                 role='HEAD_TEACHER', school=school_a)
        User.objects.create_user(username='head_y', password='x',
                                 role='HEAD_TEACHER', school=school_b)
        User.objects.create_user(username='cash_z', password='x',
                                 role='CASHIER', school=school_a)
        client.force_login(proprietor)
        resp = client.get(reverse('dashboard'))
        assert resp.context['combined_teachers'] == 2
        assert resp.context['combined_staff'] == 3
        rows = {r['school'].pk: r for r in resp.context['per_school']}
        assert rows[school_a.pk]['teachers'] == 1
        assert rows[school_a.pk]['staff'] == 2
        assert rows[school_b.pk]['staff'] == 1

    def test_combined_totals_equal_sum_of_branches(
            self, client, proprietor, school_a, school_b):
        build_school(school_a, 'DP', amount='100.00')
        build_school(school_b, 'DQ', amount='200.00')
        client.force_login(proprietor)
        resp = client.get(reverse('dashboard'))
        ctx = resp.context
        assert ctx['combined_expected'] == sum(r['expected'] for r in ctx['per_school'])
        assert ctx['combined_students'] == sum(r['students'] for r in ctx['per_school'])
        assert ctx['combined_outstanding'] == ctx['combined_expected'] - ctx['combined_collected']

    def test_unassigned_records_are_flagged_in_combined_view(
            self, client, proprietor, school_a):
        build_school(school_a, 'DO')
        legacy = SchoolClass.objects.create(name='Legacy', code='LEGD')
        Student.objects.create(
            first_name='Old', last_name='Pupil', school_class=legacy,
            parent_name='P', parent_phone='0240000000')
        client.force_login(proprietor)
        resp = client.get(reverse('dashboard'))
        assert resp.context['unassigned'] is not None
        assert resp.context['unassigned']['students'] >= 1
        client.post(reverse('switch_school'), {'school': school_a.pk})
        resp = client.get(reverse('dashboard'))
        assert resp.context['unassigned'] is None

    def test_switch_school_unknown_school_is_rejected(self, client, proprietor):
        client.force_login(proprietor)
        resp = client.post(reverse('switch_school'), {'school': 999999})
        assert resp.status_code == 302
        assert client.session.get(SESSION_KEY) is None


# --------------------------------------------------------------------------- #
# Staff / teacher list isolation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestStaffList:
    def test_head_teacher_sees_only_own_school_staff(
            self, client, head_a, head_b, school_a, school_b):
        client.force_login(head_a)
        resp = client.get(reverse('school_staff_list'))
        assert resp.status_code == 200
        members = list(resp.context['staff'])
        assert members and all(m.school_id == school_a.pk for m in members)
        assert all(m.pk != head_b.pk for m in members)

    def test_proprietor_sees_all_staff(self, client, proprietor, head_a, head_b):
        client.force_login(proprietor)
        resp = client.get(reverse('school_staff_list'))
        pks = {m.pk for m in resp.context['staff']}
        assert {head_a.pk, head_b.pk} <= pks

    def test_customer_cannot_open_staff_list(self, client):
        customer = User.objects.create_user(
            username='cust', password='x', role='CUSTOMER')
        client.force_login(customer)
        resp = client.get(reverse('school_staff_list'))
        assert resp.status_code == 302


# --------------------------------------------------------------------------- #
# school_status command
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestSchoolStatusCommand:
    def test_status_renders_and_reconciles(self, school_a, school_b):
        build_school(school_a, 'SA', amount='100.00')
        build_school(school_b, 'SB', amount='200.00')
        out = StringIO()
        call_command('school_status', stdout=out)
        text = out.getvalue()
        assert school_a.name in text
        assert school_b.name in text
        assert 'OK: branch totals reconcile' in text

    def test_status_flags_unassigned_records(self, school_a):
        build_school(school_a, 'SC')
        legacy = SchoolClass.objects.create(name='Legacy', code='LEGSTAT')
        Student.objects.create(
            first_name='Old', last_name='Pupil', school_class=legacy,
            parent_name='P', parent_phone='0240000000')
        out = StringIO()
        call_command('school_status', '--list-unassigned', stdout=out)
        text = out.getvalue()
        assert 'needs review' in text
        assert 'assign_school_data' in text
        assert 'Legacy' not in text or 'Students:' in text


# --------------------------------------------------------------------------- #
# Financial dashboard: legacy school-ownership repair
#
# A fee account / payment / structure that predates the branch assignment of
# its student carries ``school = NULL``. The dashboards filter by that column,
# so the record was visible under "Combined" but missing from the branch view.
# These tests reproduce the stale rows, run the ownership backfill that the
# data migration uses, and assert the branch dashboards then reconcile.
# --------------------------------------------------------------------------- #
def _repair_school_links(dry_run=False):
    return backfill_school_from_ownership({
        'Student': Student,
        'SchoolClass': SchoolClass,
        'AcademicYear': AcademicYear,
        'StudentFeeAccount': StudentFeeAccount,
        'FeePayment': FeePayment,
        'FeeStructure': FeeStructure,
        'ReminderLog': ReminderLog,
    }, dry_run=dry_run)


def _branch_with_stale_payment(school, suffix, amount, paid):
    """Build a branch then clear the derived school keys (legacy state)."""
    data = build_school(school, suffix, amount=amount)
    payment = payment_service.record_payment(data.account, Decimal(paid))
    StudentFeeAccount.objects.filter(pk=data.account.pk).update(school=None)
    FeePayment.objects.filter(pk=payment.pk).update(school=None)
    FeeStructure.objects.filter(school_class=data.school_class).update(school=None)
    return data, payment


@pytest.mark.django_db
class TestFinancialOwnershipRepair:
    def test_backfill_repairs_account_payment_and_structure(self, school_a):
        data, payment = _branch_with_stale_payment(school_a, 'FB', '100.00', '40.00')
        assert StudentFeeAccount.objects.get(pk=data.account.pk).school_id is None
        assert FeePayment.objects.get(pk=payment.pk).school_id is None
        assert FeeStructure.objects.get(school_class=data.school_class).school_id is None

        report = _repair_school_links()

        assert report['StudentFeeAccount'] == 1
        assert report['FeePayment'] == 1
        assert report['FeeStructure'] == 1
        assert StudentFeeAccount.objects.get(pk=data.account.pk).school_id == school_a.pk
        assert FeePayment.objects.get(pk=payment.pk).school_id == school_a.pk
        assert FeeStructure.objects.get(school_class=data.school_class).school_id == school_a.pk

    def test_backfill_preserves_financial_values_and_is_idempotent(self, school_a):
        data, payment = _branch_with_stale_payment(school_a, 'FC', '250.00', '110.00')
        before = StudentFeeAccount.objects.get(pk=data.account.pk)
        before_total, before_paid = before.total_fees, before.amount_paid
        before_receipt = FeePayment.objects.get(pk=payment.pk)
        before_receipt_no = before_receipt.receipt_number
        before_amount = before_receipt.amount

        first = _repair_school_links()
        second = _repair_school_links()

        assert first['StudentFeeAccount'] == 1
        assert second == {
            'Student': 0, 'StudentFeeAccount': 0, 'FeePayment': 0,
            'FeeStructure': 0, 'ReminderLog': 0,
        }
        after = StudentFeeAccount.objects.get(pk=data.account.pk)
        after_receipt = FeePayment.objects.get(pk=payment.pk)
        assert after.total_fees == before_total
        assert after.amount_paid == before_paid
        assert after_receipt.receipt_number == before_receipt_no
        assert after_receipt.amount == before_amount
        assert after.payments.count() == 1

    def test_backfill_does_not_guess_truly_unassigned_records(self, school_a):
        build_school(school_a, 'FD')
        legacy_class = SchoolClass.objects.create(name='Legacy', code='LEG-FD')
        student = Student.objects.create(
            first_name='Old', last_name='Pupil', school_class=legacy_class,
            parent_name='P', parent_phone='0240000000')

        report = _repair_school_links()

        assert report['Student'] == 0
        assert Student.objects.get(pk=student.pk).school_id is None
        assert SchoolClass.objects.get(pk=legacy_class.pk).school_id is None

    def test_student_save_self_heals_financial_records(self, school_a):
        data, payment = _branch_with_stale_payment(school_a, 'FE', '100.00', '10.00')
        assert StudentFeeAccount.objects.get(pk=data.account.pk).school_id is None

        Student.objects.get(pk=data.student.pk).save()

        assert StudentFeeAccount.objects.get(pk=data.account.pk).school_id == school_a.pk
        assert FeePayment.objects.get(pk=payment.pk).school_id == school_a.pk


@pytest.mark.django_db
class TestBranchFinancialDashboards:
    def test_switched_branch_shows_its_records_and_combined_does_not_double_count(
            self, client, proprietor, school_a, school_b):
        a, pay_a = _branch_with_stale_payment(school_a, 'FA', '100.00', '40.00')
        b, pay_b = _branch_with_stale_payment(school_b, 'FB2', '200.00', '60.00')
        _repair_school_links()

        client.force_login(proprietor)

        # Combined: both branches, aggregated exactly once.
        ctx = client.get(reverse('dashboard')).context
        assert ctx['combined_expected'] == Decimal('300.00')
        assert ctx['combined_collected'] == Decimal('100.00')
        assert ctx['combined_outstanding'] == Decimal('200.00')
        assert ctx['combined_payments'] == Decimal('100.00')
        assert ctx['combined_students'] == 2
        assert ctx['combined_expected'] == sum(r['expected'] for r in ctx['per_school'])
        assert ctx['combined_collected'] == sum(r['collected'] for r in ctx['per_school'])

        # Switched to branch A: only A's records, same figures as its row.
        client.post(reverse('switch_school'), {'school': school_a.pk})
        ctx = client.get(reverse('dashboard')).context
        assert len(ctx['per_school']) == 1
        row = ctx['per_school'][0]
        assert row['school'].pk == school_a.pk
        assert row['students'] == 1
        assert row['expected'] == Decimal('100.00')
        assert row['collected'] == Decimal('40.00')
        assert row['outstanding'] == Decimal('60.00')
        assert ctx['combined_expected'] == Decimal('100.00')
        assert ctx['combined_collected'] == Decimal('40.00')
        assert ctx['combined_payments'] == Decimal('40.00')

        # Switched to branch B: only B's records.
        client.post(reverse('switch_school'), {'school': school_b.pk})
        ctx = client.get(reverse('dashboard')).context
        assert len(ctx['per_school']) == 1
        row = ctx['per_school'][0]
        assert row['school'].pk == school_b.pk
        assert row['expected'] == Decimal('200.00')
        assert row['collected'] == Decimal('60.00')
        assert ctx['combined_expected'] == Decimal('200.00')
        assert ctx['combined_collected'] == Decimal('60.00')
        assert ctx['combined_payments'] == Decimal('60.00')

    def test_head_teacher_fee_dashboard_shows_repaired_totals(
            self, client, head_a, school_a, school_b):
        _branch_with_stale_payment(school_a, 'FE2', '100.00', '40.00')
        _branch_with_stale_payment(school_b, 'FF', '200.00', '60.00')
        _repair_school_links()

        client.force_login(head_a)
        resp = client.get(reverse('school_fees_dashboard'))
        assert resp.status_code == 200
        ctx = resp.context
        assert ctx['total_students'] == 1
        assert ctx['total_fees'] == Decimal('100.00')
        assert ctx['total_collected'] == Decimal('40.00')
        assert ctx['total_outstanding'] == Decimal('60.00')


