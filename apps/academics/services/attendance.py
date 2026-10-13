"""Attendance recording and summaries."""

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from ..models import (
    AttendanceCorrection,
    AttendanceRecord,
    AttendanceSession,
)


def get_or_create_session(*, school_class, subject, academic_year, term, date,
                          session_label='Daily', user=None):
    """Return the session for a class/subject/date, creating it if needed.

    This is the single entry point that guarantees a class cannot have two
    sessions for the same date and subject/session label.
    """
    session, created = AttendanceSession.objects.get_or_create(
        school_class=school_class,
        subject=subject,
        academic_year=academic_year,
        term=term,
        date=date,
        session_label=session_label or 'Daily',
        defaults={'taken_by': user, 'school': school_class.school},
    )
    if not created and user is not None and session.taken_by_id is None:
        session.taken_by = user
        session.save(update_fields=['taken_by'])
    return session, created


@transaction.atomic
def save_attendance(session, entries, user=None, reason=''):
    """Persist attendance entries for ``session``.

    ``entries`` is an iterable of ``(student, status, note)``. Existing records
    are updated and every status change is written to the correction audit
    trail, so corrections are always attributable.
    """
    saved = 0
    for student, status, note in entries:
        if status not in dict(AttendanceRecord.Status.choices):
            continue
        record = AttendanceRecord.objects.filter(
            session=session, student=student).first()
        if record is None:
            AttendanceRecord.objects.create(
                session=session, student=student, status=status, note=note or '')
            saved += 1
            continue
        if record.status != status or (note or '') != (record.note or ''):
            if record.status != status:
                AttendanceCorrection.objects.create(
                    record=record, old_status=record.status, new_status=status,
                    reason=reason or 'Attendance corrected', corrected_by=user)
            record.status = status
            if note is not None:
                record.note = note
            record.save(update_fields=['status', 'note', 'updated_at'])
            saved += 1
    return saved


def session_summary(session):
    """Counts per status for a single session."""
    counts = {choice: 0 for choice, _ in AttendanceRecord.Status.choices}
    for status in session.records.values_list('status', flat=True):
        counts[status] = counts.get(status, 0) + 1
    return {
        'present': counts['PRESENT'],
        'absent': counts['ABSENT'],
        'late': counts['LATE'],
        'excused': counts['EXCUSED'],
        'total': sum(counts.values()),
    }


def student_summary(student, academic_year=None, term=None):
    """Attendance counts/percentage for a student (optionally per term)."""
    qs = AttendanceRecord.objects.filter(student=student)
    if academic_year is not None:
        qs = qs.filter(session__academic_year=academic_year)
    if term is not None:
        qs = qs.filter(session__term=term)
    counts = {choice: 0 for choice, _ in AttendanceRecord.Status.choices}
    for status in qs.values_list('status', flat=True):
        counts[status] = counts.get(status, 0) + 1
    total = sum(counts.values())
    attended = counts['PRESENT'] + counts['LATE']
    percentage = None
    if total:
        percentage = (Decimal(attended) / Decimal(total) * Decimal('100')).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP)
    return {
        'present': counts['PRESENT'],
        'absent': counts['ABSENT'],
        'late': counts['LATE'],
        'excused': counts['EXCUSED'],
        'total': total,
        'attended': attended,
        'percentage': percentage,
    }


def class_summary(school_class, academic_year, term):
    """Attendance percentage for every student in a class for a term."""
    qs = AttendanceRecord.objects.filter(
        session__school_class=school_class,
        session__academic_year=academic_year,
        session__term=term,
    )
    per_student = {}
    for record in qs.select_related('student'):
        bucket = per_student.setdefault(record.student_id, {
            'student': record.student, 'present': 0, 'absent': 0, 'late': 0,
            'excused': 0, 'total': 0})
        key = record.status.lower()
        bucket[key] = bucket.get(key, 0) + 1
        bucket['total'] += 1
    rows = []
    for bucket in per_student.values():
        attended = bucket['present'] + bucket['late']
        bucket['attended'] = attended
        bucket['percentage'] = (
            (Decimal(attended) / Decimal(bucket['total']) * Decimal('100')).quantize(
                Decimal('0.1'), rounding=ROUND_HALF_UP)
            if bucket['total'] else None)
        rows.append(bucket)
    rows.sort(key=lambda r: r['student'].get_full_name().lower())
    return rows
