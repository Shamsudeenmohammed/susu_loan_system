"""Assign legacy (school-less) school-fees records to a school.

Records created before multi-school support have ``school = NULL``. They stay
visible to global users (proprietor / admin) but are hidden from school-scoped
(HEAD_TEACHER) users. Run this command to attach such records to a school once
you know which branch they belong to.

    python manage.py assign_school_data MANKARIGU
    python manage.py assign_school_data LINGBINSI --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.schools.models import School
from apps.school_fees.models import (
    AcademicYear,
    FeeCategory,
    FeePayment,
    FeeStructure,
    ReminderLog,
    SchoolClass,
    Student,
    StudentFeeAccount,
)


class Command(BaseCommand):
    help = 'Attach school-less (legacy) school-fees records to a school.'

    def add_arguments(self, parser):
        parser.add_argument('school', help='School code (e.g. MANKARIGU).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would change without saving.')

    @transaction.atomic
    def handle(self, *args, **options):
        code = options['school']
        school = School.objects.filter(code__iexact=code).first()
        if school is None:
            raise CommandError(f"No school with code '{code}'. "
                               f"Available: {list(School.objects.values_list('code', flat=True))}")

        dry = options['dry_run']
        # Order matters: parents first so children can inherit the school.
        models = [
            ('SchoolClass', SchoolClass),
            ('AcademicYear', AcademicYear),
            ('FeeCategory', FeeCategory),
            ('Student', Student),
            ('FeeStructure', FeeStructure),
            ('StudentFeeAccount', StudentFeeAccount),
            ('FeePayment', FeePayment),
            ('ReminderLog', ReminderLog),
        ]

        total = 0
        for label, model in models:
            qs = model.objects.filter(school__isnull=True)
            count = qs.count()
            total += count
            if count and not dry:
                qs.update(school=school)
            self.stdout.write(f'{label}: {count} record(s) -> {school.code}')

        if dry:
            self.stdout.write(self.style.WARNING(
                f'Dry run: {total} record(s) would be reassigned.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'{total} record(s) assigned to {school.name}.'))
