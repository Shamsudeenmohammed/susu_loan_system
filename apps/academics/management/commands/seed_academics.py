"""Seed default academic configuration for each school.

Creates (idempotently):
  * a default grading scale with standard bands,
  * an ``AcademicSettings`` row,
  * the terminal-report financial field rows.

Safe to run repeatedly: it never overwrites existing configuration.
"""

from django.core.management.base import BaseCommand

from apps.academics.models import (
    AcademicSettings,
    GradeBand,
    GradingScale,
    TerminalReportField,
)
from apps.schools.models import School

DEFAULT_BANDS = [
    ('A1', 'Excellent', 80, 100),
    ('B2', 'Very good', 70, 79.99),
    ('B3', 'Good', 65, 69.99),
    ('C4', 'Credit', 60, 64.99),
    ('C5', 'Credit', 55, 59.99),
    ('C6', 'Credit', 50, 54.99),
    ('D7', 'Pass', 45, 49.99),
    ('E8', 'Pass', 40, 44.99),
    ('F9', 'Fail', 0, 39.99),
]

# (key, display_order, is_visible)
DEFAULT_REPORT_FIELDS = [
    ('TOTAL_FEES', 10, True),
    ('ARREARS', 20, True),
    ('AMOUNT_PAID', 30, True),
    ('OUTSTANDING', 40, True),
    ('CREDIT', 50, True),
    ('ACCOUNT_BALANCE', 60, False),
    ('DISCOUNT', 70, False),
    ('SCHOLARSHIP', 80, False),
    ('WAIVER', 90, False),
    ('REFUND', 100, False),
]


def seed_school(school, verbosity=1):
    scale = GradingScale.objects.filter(school=school).first()
    if scale is None:
        scale = GradingScale.objects.create(
            school=school, name='Standard Grading Scale', is_default=True)
        for order, (grade, description, low, high) in enumerate(DEFAULT_BANDS):
            GradeBand.objects.create(
                scale=scale, grade=grade, description=description,
                min_score=low, max_score=high, order=order)
        if verbosity:
            print(f'  {school.code}: created grading scale with bands')

    settings_obj, created = AcademicSettings.objects.get_or_create(
        school=school, defaults={'default_grading_scale': scale})
    if created and verbosity:
        print(f'  {school.code}: created academic settings')

    existing = set(TerminalReportField.objects.filter(
        school=school).values_list('key', flat=True))
    for key, order, visible in DEFAULT_REPORT_FIELDS:
        if key not in existing:
            TerminalReportField.objects.create(
                school=school, key=key, display_order=order, is_visible=visible)
    if verbosity:
        print(f'  {school.code}: terminal report fields ensured')


class Command(BaseCommand):
    help = 'Seed default grading scales, academic settings and report fields.'

    def add_arguments(self, parser):
        parser.add_argument('--school', help='Only seed this school code.')

    def handle(self, *args, **options):
        schools = School.objects.filter(is_active=True)
        if options.get('school'):
            schools = schools.filter(code=options['school'])
        if not schools.exists():
            self.stdout.write(self.style.WARNING('No matching schools found.'))
            return
        for school in schools:
            seed_school(school, verbosity=options.get('verbosity', 1))
        self.stdout.write(self.style.SUCCESS('Academic configuration seeded.'))
