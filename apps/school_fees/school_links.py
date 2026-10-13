"""Repair missing school ownership on school-fees records from reliable parentage.

Records created before multi-school support can have ``school = NULL`` even
though the record has a clear owner — for example a fee account whose student
now belongs to a branch, or a fee payment whose account does. The per-school
dashboards filter these financial tables by their own ``school`` column, so such
rows are counted in the combined view but omitted from every branch view.

This module derives the school from that ownership instead of guessing:

* a student inherits its class's school;
* a fee account inherits its student's school;
* a fee payment inherits its account's, then its student's, school;
* a fee structure inherits its class's, then its academic year's, school;
* a reminder log inherits its student's school.

It only ever fills a NULL school. A record that already has a school is never
touched, and records with no resolvable owner (e.g. an academic year or a
category shared by both branches) are deliberately left unassigned so nothing is
mis-attributed.
"""


def backfill_school_from_ownership(models, dry_run=False):
    """Fill NULL ``school`` values from reliable parentage.

    ``models`` maps model names to model classes (the historical models in a
    migration, or the concrete models elsewhere). Returns a ``{label: count}``
    report of how many records were (or would be) repaired.
    """
    Student = models['Student']
    SchoolClass = models['SchoolClass']
    AcademicYear = models['AcademicYear']
    StudentFeeAccount = models['StudentFeeAccount']
    FeePayment = models['FeePayment']
    FeeStructure = models['FeeStructure']
    ReminderLog = models['ReminderLog']

    report = {}

    def _fill(label, model, objects, resolver):
        changed = 0
        for obj in objects:
            if obj.school_id is not None:
                continue
            school_id = resolver(obj)
            if not school_id:
                continue
            changed += 1
            if not dry_run:
                model.objects.filter(pk=obj.pk, school__isnull=True).update(
                    school_id=school_id)
        report[label] = changed

    # Snapshot the parent schools up front (small lookup tables).
    class_school = dict(
        SchoolClass.objects.exclude(school__isnull=True)
        .values_list('pk', 'school_id'))
    year_school = dict(
        AcademicYear.objects.exclude(school__isnull=True)
        .values_list('pk', 'school_id'))

    # 1. Students inherit from their class.
    _fill('Student', Student,
          list(Student.objects.filter(school__isnull=True)),
          lambda o: class_school.get(o.school_class_id))
    student_school = dict(
        Student.objects.exclude(school__isnull=True)
        .values_list('pk', 'school_id'))

    # 2. Fee accounts inherit from their student.
    _fill('StudentFeeAccount', StudentFeeAccount,
          list(StudentFeeAccount.objects.filter(school__isnull=True)),
          lambda o: student_school.get(o.student_id))
    account_school = dict(
        StudentFeeAccount.objects.exclude(school__isnull=True)
        .values_list('pk', 'school_id'))

    # 3. Fee payments inherit from their account, then their student.
    _fill('FeePayment', FeePayment,
          list(FeePayment.objects.filter(school__isnull=True)),
          lambda o: account_school.get(o.account_id) or student_school.get(o.student_id))

    # 4. Fee structures inherit from their class, then their academic year.
    _fill('FeeStructure', FeeStructure,
          list(FeeStructure.objects.filter(school__isnull=True)),
          lambda o: class_school.get(o.school_class_id) or year_school.get(o.academic_year_id))

    # 5. Reminder logs inherit from their student.
    _fill('ReminderLog', ReminderLog,
          list(ReminderLog.objects.filter(school__isnull=True)),
          lambda o: student_school.get(o.student_id))

    return report
