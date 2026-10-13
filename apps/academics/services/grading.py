"""Grade and result calculation.

All rules are read from each school's configuration (``AcademicSettings`` and
``GradingScale``); nothing here hard-codes weights, pass marks or grade bands.
Missing, absent and exempt marks are handled distinctly and are never silently
treated as zero.
"""

from decimal import Decimal, ROUND_HALF_UP

from apps.school_fees.models import Student

from ..models import AcademicSettings, Assessment, GradeBand, GradingScale

HUNDRED = Decimal('100')


def resolve_scale(school, scale=None):
    """Return the grading scale to use for ``school`` (or ``scale``)."""
    if scale is not None:
        return scale
    settings_obj = AcademicSettings.for_school(school)
    if settings_obj is None:
        return None
    return settings_obj.resolve_scale()


def find_band(scale, percentage):
    """Return the :class:`GradeBand` matching ``percentage`` (or ``None``)."""
    if scale is None or percentage is None:
        return None
    percentage = Decimal(percentage)
    for band in scale.bands.all():
        if band.matches(percentage):
            return band
    return None


def _quantize(value):
    if value is None:
        return None
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def subject_components(student, assessments, marks=None):
    """Build the component list for a student across ``assessments``.

    Returns a list of dicts describing each assessment and the student's mark,
    including whether it counts toward the weighted total. A component is
    *countable* only when it has a numeric score; absent/exempt marks are
    excluded from the denominator but reported so the result can be flagged.
    """
    if marks is None:
        marks = {
            m.assessment_id: m
            for m in student.marks.filter(assessment__in=assessments)
        }
    components = []
    for assessment in assessments:
        mark = marks.get(assessment.pk)
        status = mark.status if mark else 'MISSING'
        score = mark.score if mark else None
        max_score = assessment.max_score or Decimal('0')
        weight = assessment.weight if assessment.weight is not None else Decimal('0')
        countable = score is not None and status == 'PRESENT' and max_score > 0
        ratio = None
        awarded = None
        if countable:
            ratio = (Decimal(score) / max_score)
            awarded = ratio * weight
        components.append({
            'assessment': assessment,
            'mark': mark,
            'status': status,
            'score': score,
            'max_score': max_score,
            'weight': weight,
            'countable': countable,
            'ratio': ratio,
            'weighted_award': awarded,
        })
    return components


def weighted_percentage(components):
    """Compute the weighted percentage for ``components``.

    ``None`` when nothing can be counted. The result is normalised by the
    weights that actually counted, so a school that weights a class test at 30
    and an exam at 70 is handled correctly, and a school with equal weights is
    handled equally well without any hard-coded assumption.
    """
    total_weight = sum((c['weight'] for c in components if c['countable']), Decimal('0'))
    if total_weight <= 0:
        # No weights configured: fall back to a plain score/max average.
        scores = [c['ratio'] for c in components if c['countable'] and c['ratio'] is not None]
        if not scores:
            return None
        return _quantize(sum(scores) / len(scores) * HUNDRED)

    awarded = sum((c['weighted_award'] for c in components if c['countable']), Decimal('0'))
    return _quantize(awarded / total_weight * HUNDRED)


def subject_result(student, academic_year, term, subject, scale=None):
    """Compute one student's result for a subject in a term."""
    assessments = list(Assessment.objects.filter(
        academic_year=academic_year, term=term, subject=subject,
        school_class=student.school_class, is_active=True,
    ).order_by('created_at'))
    components = subject_components(student, assessments)
    percentage = weighted_percentage(components)
    band = find_band(scale, percentage)
    countable = [c for c in components if c['countable']]
    incomplete = any(c['status'] == 'MISSING' for c in components)
    absent = any(c['status'] == 'ABSENT' for c in components)
    exempt = any(c['status'] == 'EXEMPT' for c in components)
    return {
        'subject': subject,
        'components': components,
        'assessment_count': len(assessments),
        'counted_count': len(countable),
        'percentage': percentage,
        'band': band,
        'grade': band.grade if band else None,
        'grade_description': band.description if band else '',
        'remark': band.remark if band else '',
        'incomplete': incomplete,
        'has_absent': absent,
        'has_exempt': exempt,
    }


def student_term_report(student, academic_year, term, scale=None, subjects=None):
    """A full subject-by-subject result set for one student in a term."""
    from ..models import ClassSubject

    if subjects is None:
        subjects = [
            cs.subject for cs in ClassSubject.objects.filter(
                school_class=student.school_class, is_active=True
            ).select_related('subject').order_by('subject__name')
        ]
    results = [
        subject_result(student, academic_year, term, subject, scale)
        for subject in subjects
    ]
    graded = [r for r in results if r['percentage'] is not None]
    total = sum((r['percentage'] for r in graded), Decimal('0')) if graded else None
    average = _quantize(total / len(graded)) if graded else None
    return {
        'student': student,
        'subjects': results,
        'graded_subject_count': len(graded),
        'total': _quantize(total) if graded else None,
        'average': average,
        'complete': all(not r['incomplete'] for r in results),
    }
