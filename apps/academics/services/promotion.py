"""Student promotion."""

from django.db import transaction

from ..models import PromotionRecord


@transaction.atomic
def promote_student(student, academic_year, decision, to_class=None, user=None,
                    notes=''):
    """Record a promotion decision and, when promoted, move the student.

    The student's ``school_class`` is the operational "current class"; the
    :class:`PromotionRecord` preserves the year-by-year history.
    """
    record, _ = PromotionRecord.objects.update_or_create(
        student=student,
        academic_year=academic_year,
        defaults={
            'from_class': student.school_class,
            'to_class': to_class,
            'decision': decision,
            'notes': notes,
            'decided_by': user,
            'school': student.school,
        })
    if decision == PromotionRecord.Decision.PROMOTED and to_class is not None:
        student.school_class = to_class
        student.save(update_fields=['school_class', 'updated_at'])
    if decision == PromotionRecord.Decision.GRADUATED:
        student.is_active = False
        student.save(update_fields=['is_active', 'updated_at'])
    return record
