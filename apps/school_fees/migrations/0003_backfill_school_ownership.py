"""Backfill missing school ownership on legacy school-fees records.

Several school-fees records were created before multi-school support (or before
their student/class was attached to a branch). They therefore carry
``school = NULL`` even though they have a clear owner. The per-school dashboards
filter these tables by their own ``school`` column, so such rows appeared under
"Combined" but were missing from the branch dashboards.

The repair is ownership based — it only ever fills a NULL school from the
record's parent and never guesses or overwrites. See
``apps.school_fees.school_links`` for the exact rules.
"""

from django.db import migrations


def backfill_school_ownership(apps, schema_editor):
    # Imported lazily so the migration keeps working even if the helper module
    # is reorganised; the helper itself only touches the models passed in.
    from apps.school_fees.school_links import backfill_school_from_ownership

    return backfill_school_from_ownership({
        'Student': apps.get_model('school_fees', 'Student'),
        'SchoolClass': apps.get_model('school_fees', 'SchoolClass'),
        'AcademicYear': apps.get_model('school_fees', 'AcademicYear'),
        'StudentFeeAccount': apps.get_model('school_fees', 'StudentFeeAccount'),
        'FeePayment': apps.get_model('school_fees', 'FeePayment'),
        'FeeStructure': apps.get_model('school_fees', 'FeeStructure'),
        'ReminderLog': apps.get_model('school_fees', 'ReminderLog'),
    })


class Migration(migrations.Migration):

    dependencies = [
        ('school_fees', '0002_alter_feecategory_options_alter_schoolclass_options_and_more'),
    ]

    operations = [
        # Data migration: intentional no-op on reverse (we never remove a
        # school that was legitimately derived from ownership).
        migrations.RunPython(backfill_school_ownership, migrations.RunPython.noop),
    ]
