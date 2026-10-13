"""Class-level result assembly, positions and publication helpers."""

from decimal import Decimal

from ..models import ClassSubject, MarkSubmission, ResultPublication
from . import grading


def class_subjects(school_class):
    return [
        cs.subject for cs in ClassSubject.objects.filter(
            school_class=school_class, is_active=True
        ).select_related('subject').order_by('subject__name')
    ]


def class_students(school_class):
    return school_class.students.filter(is_active=True).order_by(
        'last_name', 'first_name')


def class_results(school_class, academic_year, term, scale=None, students=None,
                  subjects=None):
    """Return ``(rows, subjects)`` with per-student term results and positions.

    Positions are only assigned when the school enables them; ranking is by
    average weighted percentage (ties share the same position).
    """
    if subjects is None:
        subjects = class_subjects(school_class)
    if students is None:
        students = list(class_students(school_class))
    else:
        students = list(students)
    scale = grading.resolve_scale(school_class.school, scale)
    settings_obj = grading.AcademicSettings.for_school(school_class.school)
    show_positions = bool(settings_obj and settings_obj.show_position)

    rows = []
    for student in students:
        report = grading.student_term_report(
            student, academic_year, term, scale=scale, subjects=subjects)
        report['position'] = None
        rows.append(report)

    if show_positions:
        ranked = sorted(
            (r for r in rows if r['average'] is not None),
            key=lambda r: r['average'], reverse=True)
        last_average = None
        last_position = 0
        for index, row in enumerate(ranked, start=1):
            if last_average is not None and row['average'] == last_average:
                row['position'] = last_position
            else:
                row['position'] = index
                last_position = index
                last_average = row['average']
    return rows, subjects


def class_analytics(rows, subjects):
    """Per-subject averages and the overall class average across ``rows``."""
    analytics = []
    for subject in subjects:
        values = []
        for row in rows:
            for result in row['subjects']:
                if result['subject'].pk == subject.pk and result['percentage'] is not None:
                    values.append(result['percentage'])
        average = (sum(values) / len(values)).quantize(Decimal('0.01')) if values else None
        analytics.append({
            'subject': subject,
            'average': average,
            'graded': len(values),
            'entered': len(rows),
        })
    averages = [r['average'] for r in rows if r['average'] is not None]
    class_average = (
        (sum(averages) / len(averages)).quantize(Decimal('0.01'))
        if averages else None)
    return {'subjects': analytics, 'class_average': class_average}


def publication_state(academic_year, term, school_class):
    """Return the :class:`ResultPublication` for a class (or ``None``)."""
    return ResultPublication.objects.filter(
        academic_year=academic_year, term=term, school_class=school_class).first()


def is_published(academic_year, term, school_class):
    return ResultPublication.objects.filter(
        academic_year=academic_year, term=term, school_class=school_class,
        is_published=True).exists()


def submission_state(academic_year, term, school_class, subject):
    return MarkSubmission.objects.filter(
        academic_year=academic_year, term=term, school_class=school_class,
        subject=subject).first()
