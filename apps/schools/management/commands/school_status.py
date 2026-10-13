"""Read-only multi-school health check.

Reports, per branch and in combination, how many school-owned records exist and
the fee totals, plus any legacy records that still have ``school = NULL`` and
therefore need manual review. It never writes to the database.

    python manage.py school_status
    python manage.py school_status --list-unassigned
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Sum

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

User = get_user_model()

MODELS = [
    ('Students', Student),
    ('Classes', SchoolClass),
    ('Academic years', AcademicYear),
    ('Fee categories', FeeCategory),
    ('Fee structures', FeeStructure),
    ('Fee accounts', StudentFeeAccount),
    ('Fee payments', FeePayment),
    ('Reminder logs', ReminderLog),
]


def _money(value):
    return value or Decimal('0.00')


class Command(BaseCommand):
    help = 'Read-only check of per-branch counts/totals and unassigned records.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--list-unassigned', action='store_true',
            help='List the primary keys of school-less records needing review.')

    def _stats(self, condition):
        counts = {label: model.objects.filter(**condition).count()
                  for label, model in MODELS}
        agg = StudentFeeAccount.objects.filter(**condition).aggregate(
            expected=Sum('total_fees'), collected=Sum('amount_paid'))
        expected = _money(agg['expected'])
        collected = _money(agg['collected'])
        return {
            'counts': counts,
            'expected': expected,
            'collected': collected,
            'outstanding': expected - collected,
        }

    def _print_counts(self, counts):
        self.stdout.write(
            f"  Students: {counts['Students']}   Classes: {counts['Classes']}   "
            f"Academic years: {counts['Academic years']}   "
            f"Fee categories: {counts['Fee categories']}")
        self.stdout.write(
            f"  Fee structures: {counts['Fee structures']}   "
            f"Fee accounts: {counts['Fee accounts']}   "
            f"Payments: {counts['Fee payments']}   "
            f"Reminder logs: {counts['Reminder logs']}")

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('Multi-school status'))
        self.stdout.write('')

        grand_expected = Decimal('0.00')
        grand_collected = Decimal('0.00')

        for school in School.objects.all().order_by('name'):
            stats = self._stats({'school': school})
            staff = (User.objects.filter(school=school, is_active=True)
                     .exclude(role='CUSTOMER'))
            flag = '' if school.is_active else '  (inactive)'
            self.stdout.write(self.style.HTTP_INFO(f'{school.name}  [{school.code}]{flag}'))
            self._print_counts(stats['counts'])
            self.stdout.write(
                f"  Head teachers: {staff.filter(role='HEAD_TEACHER').count()}   "
                f"Total staff: {staff.count()}")
            self.stdout.write(
                f"  Fees expected: {stats['expected']}   "
                f"Collected: {stats['collected']}   "
                f"Outstanding: {stats['outstanding']}")
            self.stdout.write('')

            grand_expected += stats['expected']
            grand_collected += stats['collected']

        unassigned = self._stats({'school__isnull': True})
        self.stdout.write(self.style.WARNING('Unassigned (no school) - needs review'))
        if not any(unassigned['counts'].values()):
            self.stdout.write('  None. Every record has a school.')
        else:
            self._print_counts(unassigned['counts'])
            self.stdout.write(
                f"  Fees expected: {unassigned['expected']}   "
                f"Collected: {unassigned['collected']}   "
                f"Outstanding: {unassigned['outstanding']}")
            self.stdout.write(
                '  Review, then assign with: python manage.py assign_school_data <CODE>')
            self.stdout.write(
                '  (Nothing is guessed automatically; ambiguous rows stay here.)')
            if options['list_unassigned']:
                for label, model in MODELS:
                    pks = list(model.objects.filter(school__isnull=True)
                               .values_list('pk', flat=True)[:50])
                    if pks:
                        self.stdout.write(f'    {label}: {pks}')
        self.stdout.write('')

        grand_expected += unassigned['expected']
        grand_collected += unassigned['collected']
        table_expected = _money(
            StudentFeeAccount.objects.aggregate(s=Sum('total_fees'))['s'])
        table_collected = _money(
            StudentFeeAccount.objects.aggregate(s=Sum('amount_paid'))['s'])

        self.stdout.write(self.style.MIGRATE_HEADING('Reconciliation'))
        self.stdout.write(
            f'  Expected fees:  branches + unassigned = {grand_expected}  |  '
            f'whole table = {table_expected}')
        self.stdout.write(
            f'  Collected fees: branches + unassigned = {grand_collected}  |  '
            f'whole table = {table_collected}')
        if grand_expected == table_expected and grand_collected == table_collected:
            self.stdout.write(self.style.SUCCESS(
                '  OK: branch totals reconcile with the whole table.'))
        else:
            self.stdout.write(self.style.ERROR(
                '  WARNING: totals do not reconcile - investigate before migrating.'))
