"""Read-only reconciliation of school-fees financial totals per school.

Run via manage.py shell (optionally with --settings=config.settings.rehearsal).
Prints per-school and combined expected/collected/outstanding plus account and
payment counts, and lists any school-less financial rows still present.
"""
from decimal import Decimal

from django.db.models import Sum

from apps.schools.models import School
from apps.school_fees.models import (
    Student, StudentFeeAccount, FeePayment, FeeStructure, SchoolClass,
)

print('=== PER SCHOOL ===')
tot_exp = Decimal('0.00')
tot_paid = Decimal('0.00')
for s in School.objects.order_by('code'):
    accs = StudentFeeAccount.objects.filter(school=s)
    agg = accs.aggregate(fees=Sum('total_fees'), paid=Sum('amount_paid'))
    exp = agg['fees'] or Decimal('0.00')
    paid = agg['paid'] or Decimal('0.00')
    pay_sum = FeePayment.objects.filter(school=s).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    tot_exp += exp
    tot_paid += paid
    print(
        f"{s.code:12} students={Student.objects.filter(school=s, is_active=True).count():>3} "
        f"accounts={accs.count():>3} expected={exp:>10} collected={paid:>10} "
        f"outstanding={exp - paid:>10} payment_sum={pay_sum:>10} "
        f"payments={FeePayment.objects.filter(school=s).count():>3}")

print('=== COMBINED (sum of per-school) ===')
print(f"expected={tot_exp} collected={tot_paid} outstanding={tot_exp - tot_paid}")

print('=== ALL ROWS (unscoped, incl. school-less) ===')
allacc = StudentFeeAccount.objects.all()
allagg = allacc.aggregate(fees=Sum('total_fees'), paid=Sum('amount_paid'))
print(
    f"accounts={allacc.count()} expected={allagg['fees'] or 0} "
    f"collected={allagg['paid'] or 0} "
    f"payment_sum={FeePayment.objects.aggregate(t=Sum('amount'))['t'] or 0} "
    f"payments={FeePayment.objects.count()}")

print('=== STILL SCHOOL-LESS (needs review) ===')
print('accounts   :', list(StudentFeeAccount.objects.filter(school__isnull=True)
                           .values_list('account_number', flat=True)))
print('payments   :', list(FeePayment.objects.filter(school__isnull=True)
                           .values_list('receipt_number', flat=True)))
print('structures :', list(FeeStructure.objects.filter(school__isnull=True)
                           .values_list('pk', flat=True)))
print('students   :', list(Student.objects.filter(school__isnull=True)
                           .values_list('student_id', flat=True)))
print('classes    :', list(SchoolClass.objects.filter(school__isnull=True)
                           .values_list('code', flat=True)))
