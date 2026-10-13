"""Mark entry, submission and review workflow.

The controlled flow is:

    assessment config -> teacher mark entry -> submit -> review/approve ->
    publish -> terminal reports

Once a class's results are published the marks are locked; only an authorized
reviewer can open a *controlled correction* (which requires a recorded reason
and unpublishes the results) before the teacher is allowed to edit again.
Approved - but not yet published - results are protected the same way: a
reviewer must reopen them with a reason before any edit is allowed.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog

from ..models import (
    Assessment,
    MarkCorrection,
    MarkSubmission,
    ResultPublication,
    StudentMark,
)
from . import notifications


class MarkingError(Exception):
    """Raised when a marking operation is not allowed."""


def submission_for(academic_year, term, school_class, subject, *, create=True):
    if create:
        submission, _ = MarkSubmission.objects.get_or_create(
            academic_year=academic_year,
            term=term,
            school_class=school_class,
            subject=subject,
            defaults={'school': school_class.school},
        )
        return submission
    return MarkSubmission.objects.filter(
        academic_year=academic_year,
        term=term,
        school_class=school_class,
        subject=subject,
    ).first()


def assert_editable(academic_year, term, school_class, subject):
    """Raise :class:`MarkingError` when marks may not be edited."""
    if ResultPublication.is_locked(academic_year, term, school_class):
        raise MarkingError(
            'Results for this class are published and locked. '
            'Ask the Head Teacher to open a correction before editing them.')
    submission = submission_for(academic_year, term, school_class, subject)
    if submission.status == MarkSubmission.Status.APPROVED:
        raise MarkingError(
            'These marks have been approved. A controlled correction with a '
            'recorded reason is required before they can be edited again.')
    return submission


@transaction.atomic
def save_marks(assessment, entries, user):
    """Persist marks for an assessment.

    ``entries`` is an iterable of ``(student, score_or_None, status)``. Scores
    above the assessment maximum are rejected. Missing/exempt/absent are stored
    distinctly and are never coerced to zero.

    When an open correction exists for the block, every before/after score
    change is appended to the correction's audit snapshot.
    """
    submission = assert_editable(
        assessment.academic_year, assessment.term,
        assessment.school_class, assessment.subject)

    valid_statuses = set(dict(StudentMark.MarkStatus.choices))
    existing = {
        mark.student_id: mark
        for mark in StudentMark.objects.filter(assessment=assessment)
    }
    changes = []
    saved = 0
    for student, score, status in entries:
        if status not in valid_statuses:
            status = StudentMark.MarkStatus.MISSING
        if score is not None and str(score) != '':
            try:
                score = Decimal(str(score))
            except Exception:
                raise MarkingError(f'Invalid score for {student}: {score!r}')
            if score < 0:
                raise MarkingError(f'Score for {student} cannot be negative.')
            if score > assessment.max_score:
                raise MarkingError(
                    f'Score for {student} cannot exceed the maximum of '
                    f'{assessment.max_score}.')
            status = StudentMark.MarkStatus.PRESENT
        else:
            score = None
        StudentMark.objects.update_or_create(
            assessment=assessment,
            student=student,
            defaults={'score': score, 'status': status, 'recorded_by': user},
        )
        saved += 1
        prior = existing.get(student.pk)
        if prior is None:
            continue
        old_score = prior.score
        new_score = score
        if status == 'PRESENT' and prior.status == 'PRESENT' and old_score == new_score:
            continue
        changes.append({
            'assessment_id': assessment.pk,
            'student_id': student.pk,
            'student': student.get_full_name(),
            'old': {
                'score': str(old_score) if old_score is not None else None,
                'status': prior.status,
            },
            'new': {
                'score': str(new_score) if new_score is not None else None,
                'status': status,
            },
        })

    _record_changes(assessment.academic_year_id, assessment.term_id,
                    assessment.school_class_id, changes, user)

    # Editing returns a submission to draft so a previously submitted set must
    # be resubmitted for review.
    if submission.status in (MarkSubmission.Status.SUBMITTED,
                             MarkSubmission.Status.RETURNED):
        submission.status = MarkSubmission.Status.DRAFT
        submission.submitted_at = None
        submission.save(update_fields=['status', 'submitted_at', 'updated_at'])
    return submission, saved


def _record_changes(academic_year_id, term_id, school_class_id, changes, user):
    """Attach before/after mark changes to any open correction for the block."""
    if not changes:
        return
    corrections = MarkCorrection.objects.filter(
        academic_year_id=academic_year_id, term_id=term_id,
        school_class_id=school_class_id, status=MarkCorrection.Status.OPEN)
    for correction in corrections:
        merged = list(correction.changes or []) + changes
        correction.changes = merged[:500]
        correction.save(update_fields=['changes'])


def submit_for_review(academic_year, term, school_class, subject, user):
    submission = submission_for(academic_year, term, school_class, subject)
    if ResultPublication.is_locked(academic_year, term, school_class):
        raise MarkingError('Results are published; submission is not allowed.')
    submission.status = MarkSubmission.Status.SUBMITTED
    submission.submitted_by = user
    submission.submitted_at = timezone.now()
    submission.review_comment = ''
    submission.save(update_fields=[
        'status', 'submitted_by', 'submitted_at', 'review_comment', 'updated_at'])
    AuditLog.log(
        AuditLog.ActionType.MARK_SUBMITTED,
        f'{school_class.name} {subject.name}: marks submitted for review.',
        user=user, object_type='academics.MarkSubmission', object_id=submission.pk)
    notifications.notify_marks_submitted(submission)
    return submission


@transaction.atomic
def review_submission(submission, approve, user, comment=''):
    """Approve or return a submitted set of marks."""
    if submission.status != MarkSubmission.Status.SUBMITTED:
        raise MarkingError('Only submitted marks can be reviewed.')
    submission.status = (
        MarkSubmission.Status.APPROVED if approve
        else MarkSubmission.Status.RETURNED)
    submission.reviewed_by = user
    submission.reviewed_at = timezone.now()
    submission.review_comment = comment or ''
    submission.save(update_fields=[
        'status', 'reviewed_by', 'reviewed_at', 'review_comment', 'updated_at'])
    AuditLog.log(
        AuditLog.ActionType.MARK_APPROVED if approve
        else AuditLog.ActionType.MARK_RETURNED,
        f'{submission.school_class.name} {submission.subject.name} - '
        f'{"approved" if approve else "returned for correction"}.',
        user=user, object_type='academics.MarkSubmission', object_id=submission.pk)
    if approve:
        _close_open_corrections(
            submission.academic_year, submission.term, submission.school_class,
            user)
    notifications.notify_submission_reviewed(submission)
    return submission


def _close_open_corrections(academic_year, term, school_class, user=None):
    """Close the open corrections for a block once results are approved again."""
    corrections = MarkCorrection.objects.filter(
        academic_year=academic_year, term=term, school_class=school_class,
        status=MarkCorrection.Status.OPEN)
    now = timezone.now()
    for correction in corrections:
        correction.status = MarkCorrection.Status.COMPLETED
        correction.completed_at = now
        if user is not None:
            correction.save(update_fields=['status', 'completed_at'])
        else:
            correction.save()
        AuditLog.log(
            AuditLog.ActionType.MARK_CORRECTION_COMPLETED,
            f'Correction for {school_class.name} {term} completed.',
            user=user, object_type='academics.MarkCorrection',
            object_id=correction.pk)


@transaction.atomic
def open_mark_correction(academic_year, term, school_class, submission=None,
                         user=None, reason=''):
    """Open a controlled correction for approved/published results.

    This is the *only* safe route back into editing approved or published
    marks: a reason is mandatory, the correction is recorded (and audited), and
    any published results are unpublished in the same transaction.

    Permission is enforced by the calling view (school scoping plus
    :func:`apps.academics.services.access.can_review`). This service only
    receives school-scoped objects from those views, so it does not re-check
    school ownership itself.
    """
    if not (reason or '').strip():
        raise MarkingError(
            'A correction reason is required to reopen approved results.')

    if submission is not None \
            and submission.status != MarkSubmission.Status.APPROVED:
        raise MarkingError(
            'Only approved results can be reopened for correction.')

    # Reopening always unlocks the block: when the class/term is published,
    # unpublish it in the same transaction so the marks can actually be edited
    # again (and the unpublish is audited with the same reason).
    if ResultPublication.is_locked(academic_year, term, school_class):
        unpublish_results(academic_year, term, school_class, user, reason=reason)

    correction = MarkCorrection.objects.create(
        school=school_class.school if school_class else getattr(submission, 'school', None),
        academic_year=academic_year, term=term, school_class=school_class,
        submission=submission, reason=reason, initiated_by=user,
        status=MarkCorrection.Status.OPEN)

    if submission is not None:
        submission.status = MarkSubmission.Status.DRAFT
        submission.submitted_at = None
        submission.save(update_fields=['status', 'submitted_at', 'updated_at'])

    AuditLog.log(
        AuditLog.ActionType.MARK_CORRECTION_OPENED,
        f'Controlled correction opened for {correction.school_class} '
        f'({correction.term}): {reason[:200]}',
        user=user, object_type='academics.MarkCorrection',
        object_id=correction.pk)
    return correction


@transaction.atomic
def publish_results(academic_year, term, school_class, user):
    publication, _ = ResultPublication.objects.get_or_create(
        academic_year=academic_year,
        term=term,
        school_class=school_class,
        defaults={'school': school_class.school},
    )
    publication.is_published = True
    publication.published_at = timezone.now()
    publication.published_by = user
    if publication.school_id is None:
        publication.school = school_class.school
    publication.save()
    AuditLog.log(
        AuditLog.ActionType.RESULT_PUBLISHED,
        f'Results published for {school_class.name} '
        f'({academic_year.name}, {term.name}).',
        user=user, object_type='academics.ResultPublication',
        object_id=publication.pk)
    notifications.notify_results_published(publication)
    return publication


@transaction.atomic
def unpublish_results(academic_year, term, school_class, user, reason=''):
    """Unpublish results. Requires a recorded ``reason``.

    Publishing again later is a normal reviewer action, but every unpublish is
    intentionally audited so approved results are never silently reopened.
    """
    if not (reason or '').strip():
        raise MarkingError(
            'A reason is required to unpublish results.')
    publication = ResultPublication.objects.filter(
        academic_year=academic_year, term=term, school_class=school_class).first()
    if publication is None:
        return None
    publication.is_published = False
    publication.published_at = None
    publication.published_by = user
    publication.save()
    AuditLog.log(
        AuditLog.ActionType.RESULT_UNPUBLISHED,
        f'Results unpublished for {school_class.name} '
        f'({academic_year.name}, {term.name}): {reason[:200]}',
        user=user, object_type='academics.ResultPublication',
        object_id=publication.pk)
    return publication


def missing_mark_count(assessment):
    return assessment.marks.filter(
        score__isnull=True, status=StudentMark.MarkStatus.MISSING).count()