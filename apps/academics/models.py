"""Academic management models.

These models layer academic structure, attendance, assessment and results on
top of the existing school-fees primitives (``School``, ``SchoolClass``,
``AcademicYear``, ``Term`` and ``Student``). They are *additive*: no existing
school-fees table is modified and no financial ledger is duplicated. Every
model is school-owned and scoped through :mod:`apps.schools.scoping`.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.school_fees.models import (
    SchoolOwnedModel,
    SchoolClass,
    AcademicYear,
    Term,
    Student,
)


# ---------------------------------------------------------------------------
# Academic configuration
# ---------------------------------------------------------------------------
class Subject(SchoolOwnedModel):
    """A subject taught within a school (e.g. Mathematics, English)."""

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    is_core = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ['school', 'code']

    def __str__(self):
        return self.name


class ClassSubject(SchoolOwnedModel):
    """A subject offered by a particular class, optionally with a teacher."""

    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='class_subjects')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name='class_subjects')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['school_class', 'subject']
        unique_together = ['school_class', 'subject']

    def __str__(self):
        return f"{self.school_class} - {self.subject}"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.school_class_id:
            self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)


class TeacherAssignment(SchoolOwnedModel):
    """Links a teacher to a class + subject for an academic year (and term).

    This is the authoritative permission source for attendance and marks: a
    teacher may only enter data for classes/subjects they are assigned to.
    """

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='teaching_assignments')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='teacher_assignments')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name='teacher_assignments')
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='teacher_assignments')
    term = models.ForeignKey(
        Term, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='teacher_assignments',
        help_text='Leave blank for a whole-year assignment.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['teacher', 'school_class', 'subject']
        unique_together = ['teacher', 'school_class', 'subject', 'academic_year', 'term']

    def __str__(self):
        return f"{self.teacher} - {self.school_class} {self.subject}"

    def save(self, *args, **kwargs):
        if self.school_id is None:
            if self.school_class_id and self.school_class.school_id:
                self.school_id = self.school_class.school_id
            elif self.academic_year_id and self.academic_year.school_id:
                self.school_id = self.academic_year.school_id
        super().save(*args, **kwargs)

    @classmethod
    def for_teacher(cls, teacher, year=None, term=None):
        qs = cls.objects.filter(teacher=teacher, is_active=True)
        if year is not None:
            qs = qs.filter(academic_year=year)
        if term is not None:
            qs = qs.filter(models.Q(term=term) | models.Q(term__isnull=True))
        return qs


class GradingScale(SchoolOwnedModel):
    """A named grading scale (e.g. "Standards Based", "WASSCE")."""

    name = models.CharField(max_length=120)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ['school', 'name']

    def __str__(self):
        return self.name


class GradeBand(models.Model):
    """A single band in a grading scale (grade, range, description, remark)."""

    scale = models.ForeignKey(
        GradingScale, on_delete=models.CASCADE, related_name='bands')
    grade = models.CharField(max_length=10)
    description = models.CharField(max_length=120, blank=True)
    min_score = models.DecimalField(max_digits=6, decimal_places=2)
    max_score = models.DecimalField(max_digits=6, decimal_places=2)
    remark = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-min_score', 'grade']
        unique_together = ['scale', 'grade']

    def __str__(self):
        return f"{self.grade} ({self.min_score}-{self.max_score})"

    def matches(self, percentage):
        if percentage is None:
            return False
        return self.min_score <= Decimal(percentage) <= self.max_score


class AcademicSettings(models.Model):
    """Per-school academic configuration (grading, weights, report options).

    One row per school. Created on demand with sensible defaults so a school
    that has never been configured still works. Nothing here is hard-coded in
    the calculation code: the service layer always reads these values.
    """

    school = models.OneToOneField(
        'schools.School', on_delete=models.CASCADE, related_name='academic_settings')

    default_grading_scale = models.ForeignKey(
        GradingScale, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+')
    pass_mark = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('50.00'))
    promotion_min_average = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('45.00'))
    continuous_assessment_weight = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('40.00'))
    exam_weight = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('60.00'))

    show_position = models.BooleanField(default=False)
    show_attendance_on_report = models.BooleanField(default=True)
    show_financials_on_report = models.BooleanField(default=True)

    currency = models.CharField(max_length=10, default='GHS')
    report_title = models.CharField(max_length=160, blank=True, default='Terminal Report')
    report_header = models.CharField(
        max_length=255, blank=True,
        help_text='Optional address/contact line shown under the school name.')
    head_teacher_title = models.CharField(max_length=120, blank=True, default='Head Teacher')
    head_teacher_name = models.CharField(max_length=150, blank=True)
    show_position_on_report = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Academic Settings'
        verbose_name_plural = 'Academic Settings'

    def __str__(self):
        return f"Academic settings - {self.school}"

    @classmethod
    def for_school(cls, school):
        if school is None:
            return None
        obj, _ = cls.objects.get_or_create(school=school)
        return obj

    def resolve_scale(self):
        if self.default_grading_scale_id:
            return self.default_grading_scale
        return GradingScale.objects.filter(school=self.school, is_active=True).first()


# ---------------------------------------------------------------------------
# Assessment & marks
# ---------------------------------------------------------------------------
class Assessment(SchoolOwnedModel):
    """A piece of assessment (test, exam, assignment...) for a class subject."""

    class AssessmentType(models.TextChoices):
        CLASS_TEST = 'CLASS_TEST', 'Class Test'
        EXAM = 'EXAM', 'Examination'
        ASSIGNMENT = 'ASSIGNMENT', 'Assignment'
        PROJECT = 'PROJECT', 'Project'
        QUIZ = 'QUIZ', 'Quiz'
        PRACTICAL = 'PRACTICAL', 'Practical'
        ORAL = 'ORAL', 'Oral'
        HOMEWORK = 'HOMEWORK', 'Homework'
        OTHER = 'OTHER', 'Other'

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='assessments')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='assessments')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='assessments')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name='assessments')
    name = models.CharField(max_length=150)
    assessment_type = models.CharField(
        max_length=20, choices=AssessmentType.choices, default=AssessmentType.CLASS_TEST)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('100.00'))
    weight = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('0.00'),
        help_text='Relative weight used to compute weighted totals. '
                  'The term percentage normalises by the weights actually used.')
    date = models.DateField(null=True, blank=True)
    marks_due_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_assessments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['academic_year', 'term', 'school_class', 'subject', 'name']
        unique_together = ['academic_year', 'term', 'school_class', 'subject', 'name']

    def __str__(self):
        return f"{self.name} ({self.subject} - {self.school_class})"

    def save(self, *args, **kwargs):
        if self.school_id is None:
            if self.school_class_id and self.school_class.school_id:
                self.school_id = self.school_class.school_id
            elif self.academic_year_id and self.academic_year.school_id:
                self.school_id = self.academic_year.school_id
        super().save(*args, **kwargs)


class StudentMark(models.Model):
    """A student's score for one assessment.

    A ``score`` of ``None`` is meaningful: combined with ``status`` it lets the
    system distinguish a genuinely missing mark from an absent or exempt
    student. Missing marks are never silently treated as zero.
    """

    class MarkStatus(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT = 'ABSENT', 'Absent'
        EXEMPT = 'EXEMPT', 'Exempt'
        MISSING = 'MISSING', 'Not entered'

    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name='marks')
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='marks')
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=MarkStatus.choices, default=MarkStatus.MISSING)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recorded_marks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student']
        unique_together = ['assessment', 'student']

    def __str__(self):
        return f"{self.student} - {self.assessment}: {self.score} ({self.status})"

    def clean(self):
        if self.score is not None:
            if self.score < 0:
                raise ValidationError({'score': 'Score cannot be negative.'})
            if self.assessment_id and self.score > self.assessment.max_score:
                raise ValidationError({
                    'score': f'Score cannot exceed the maximum of '
                             f'{self.assessment.max_score}.'})


class MarkSubmission(SchoolOwnedModel):
    """The submission/review state of a class-subject's marks for a term."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SUBMITTED = 'SUBMITTED', 'Submitted for review'
        APPROVED = 'APPROVED', 'Approved'
        RETURNED = 'RETURNED', 'Returned for correction'

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='mark_submissions')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='mark_submissions')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='mark_submissions')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name='mark_submissions')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mark_submissions')
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_mark_submissions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        unique_together = ['academic_year', 'term', 'school_class', 'subject']

    def __str__(self):
        return f"{self.school_class} {self.subject} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.school_class_id:
            self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)


class ResultPublication(SchoolOwnedModel):
    """Gate that locks a class's results once they are published."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='result_publications')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='result_publications')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='result_publications')
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='result_publications')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-published_at']
        unique_together = ['academic_year', 'term', 'school_class']

    def __str__(self):
        state = 'Published' if self.is_published else 'Unpublished'
        return f"{self.school_class} {self.term} - {state}"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.school_class_id:
            self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)

    @classmethod
    def is_locked(cls, year, term, school_class):
        return cls.objects.filter(
            academic_year=year, term=term, school_class=school_class,
            is_published=True).exists()


class TerminalComment(SchoolOwnedModel):
    """Class-teacher comment, head remarks and promotion decision per student."""

    class PromotionStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROMOTED = 'PROMOTED', 'Promoted'
        REPEATED = 'REPEATED', 'Repeat'
        GRADUATED = 'GRADUATED', 'Completed'

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='terminal_comments')
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='terminal_comments')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='terminal_comments')
    class_teacher_comment = models.TextField(blank=True)
    head_teacher_remark = models.TextField(blank=True)
    promotion_status = models.CharField(
        max_length=10, choices=PromotionStatus.choices, default=PromotionStatus.PENDING)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='terminal_comments')
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['student']
        unique_together = ['student', 'academic_year', 'term']

    def __str__(self):
        return f"{self.student} - {self.academic_year} {self.term}"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.student_id:
            self.school_id = self.student.school_id
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
class AttendanceSession(SchoolOwnedModel):
    """A single attendance-taking event (daily or per lesson/subject)."""

    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='attendance_sessions')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, null=True, blank=True,
        related_name='attendance_sessions',
        help_text='Blank for whole-class daily attendance.')
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='attendance_sessions')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='attendance_sessions')
    date = models.DateField(default=timezone.localdate)
    session_label = models.CharField(max_length=40, default='Daily')
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='attendance_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', 'school_class']
        indexes = [models.Index(fields=['school_class', 'date'])]

    def __str__(self):
        subject = self.subject.code if self.subject_id else 'Daily'
        return f"{self.school_class} {subject} {self.date} ({self.session_label})"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.school_class_id:
            self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)


class AttendanceRecord(models.Model):
    """One student's attendance status within a session."""

    class Status(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT = 'ABSENT', 'Absent'
        LATE = 'LATE', 'Late'
        EXCUSED = 'EXCUSED', 'Excused'

    session = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name='records')
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='attendance_records')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['student__last_name', 'student__first_name']
        unique_together = ['session', 'student']

    def __str__(self):
        return f"{self.student} - {self.get_status_display()}"


class AttendanceCorrection(models.Model):
    """Audit trail for an authorized change to an attendance record."""

    record = models.ForeignKey(
        AttendanceRecord, on_delete=models.CASCADE, related_name='corrections')
    old_status = models.CharField(max_length=10)
    new_status = models.CharField(max_length=10)
    reason = models.CharField(max_length=300, blank=True)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='attendance_corrections')
    corrected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-corrected_at']

    def __str__(self):
        return f"{self.record} {self.old_status}→{self.new_status}"


# ---------------------------------------------------------------------------
# Promotion & calendar & communication
# ---------------------------------------------------------------------------
class PromotionRecord(SchoolOwnedModel):
    """A student's end-of-year promotion decision and class history."""

    class Decision(models.TextChoices):
        PROMOTED = 'PROMOTED', 'Promoted'
        REPEATED = 'REPEATED', 'Repeated'
        GRADUATED = 'GRADUATED', 'Graduated'
        PENDING = 'PENDING', 'Pending'

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name='promotion_records')
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='promotion_records')
    from_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='promotions_from')
    to_class = models.ForeignKey(
        SchoolClass, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='promotions_to')
    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)
    notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='promotion_decisions')
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-decided_at']
        unique_together = ['student', 'academic_year']

    def __str__(self):
        return f"{self.student}: {self.get_decision_display()}"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.student_id:
            self.school_id = self.student.school_id
        super().save(*args, **kwargs)


class AcademicCalendarEvent(SchoolOwnedModel):
    """A school calendar entry (term dates, exams, holidays, deadlines)."""

    class EventType(models.TextChoices):
        TERM = 'TERM', 'Term'
        EXAM = 'EXAM', 'Examination'
        HOLIDAY = 'HOLIDAY', 'Holiday'
        DEADLINE = 'DEADLINE', 'Deadline'
        EVENT = 'EVENT', 'School Event'

    title = models.CharField(max_length=200)
    event_type = models.CharField(max_length=10, choices=EventType.choices, default=EventType.EVENT)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_all_day = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='calendar_events')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_date']
        indexes = [models.Index(fields=['school', 'start_date'])]

    def __str__(self):
        return f"{self.title} ({self.start_date})"


class Announcement(SchoolOwnedModel):
    """An in-app announcement scoped to a school and audience."""

    class Audience(models.TextChoices):
        ALL = 'ALL', 'Everyone'
        TEACHERS = 'TEACHERS', 'Teachers'
        HEAD_TEACHERS = 'HEAD_TEACHERS', 'Head Teachers'
        PARENTS = 'PARENTS', 'Parents'

    title = models.CharField(max_length=200)
    body = models.TextField()
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.ALL)
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='announcements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-published_at']

    def __str__(self):
        return self.title


class TerminalReportField(SchoolOwnedModel):
    """Configures which financial fields appear on a school's terminal report.

    Only presentation is configured here — never the underlying amounts. This
    keeps the report configurable per school (Mankarigu vs Lingbinsi) without
    duplicating or altering the fees ledger.
    """

    class FieldKey(models.TextChoices):
        TOTAL_FEES = 'TOTAL_FEES', 'Total fees payable'
        ARREARS = 'ARREARS', 'Previous outstanding arrears'
        AMOUNT_PAID = 'AMOUNT_PAID', 'Amount paid'
        DISCOUNT = 'DISCOUNT', 'Discounts'
        SCHOLARSHIP = 'SCHOLARSHIP', 'Scholarships'
        WAIVER = 'WAIVER', 'Waivers'
        REFUND = 'REFUND', 'Refunds'
        OUTSTANDING = 'OUTSTANDING', 'Outstanding fees'
        CREDIT = 'CREDIT', 'Advance payment / credit'
        ACCOUNT_BALANCE = 'ACCOUNT_BALANCE', 'Student account balance'

    key = models.CharField(max_length=30, choices=FieldKey.choices)
    label = models.CharField(max_length=120, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'pk']
        unique_together = ['school', 'key']

    def __str__(self):
        return f"{self.school} - {self.get_key_display()}"

    @property
    def resolved_label(self):
        return self.label or self.get_key_display()


# ---------------------------------------------------------------------------
# Head Teacher update requests & follow-ups
# ---------------------------------------------------------------------------
class UpdateRequest(SchoolOwnedModel):
    """A request from the Head Teacher to a responsible teacher.

    The request centre lets the Head Teacher ask a teacher (or otherwise
    authorized staff member) to complete a legitimate academic task - e.g.
    finish missing attendance, correct a record, verify marks, or complete a
    result submission. Every request carries its category, priority, due date,
    status, the teacher it is assigned to, and a full event history.

    Statuses:

    * ``PENDING`` - created, awaiting the teacher to begin.
    * ``IN_PROGRESS`` - the teacher started working on it.
    * ``SUBMITTED`` - the teacher finished and asked the Head Teacher to
      confirm.
    * ``COMPLETED`` - the Head Teacher confirmed the work.
    * ``CANCELLED`` - the Head Teacher cancelled it (or a request was re-opened
      only to be cancelled again).

    A re-opened request returns to ``PENDING`` (or ``IN_PROGRESS``) so the
    teacher can continue; ``completed_at`` is cleared.
    """

    class Category(models.TextChoices):
        ATTENDANCE_MISSING = 'ATTENDANCE_MISSING', 'Complete missing attendance'
        ATTENDANCE_CORRECTION = 'ATTENDANCE_CORRECTION', 'Correct attendance'
        MARKS_VERIFICATION = 'MARKS_VERIFICATION', 'Verify or correct marks'
        RESULTS_SUBMISSION = 'RESULTS_SUBMISSION', 'Complete result submission'
        ACADEMIC_INFO = 'ACADEMIC_INFO', 'Update missing academic information'
        TERMINAL_REPORT = 'TERMINAL_REPORT', 'Review terminal report'
        OTHER = 'OTHER', 'Other academic task'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    title = models.CharField(max_length=180)
    category = models.CharField(max_length=30, choices=Category.choices,
                                default=Category.OTHER)
    description = models.TextField(blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='requested_update_requests')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='assigned_update_requests')

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, null=True, blank=True,
        related_name='update_requests')

    priority = models.CharField(max_length=10, choices=Priority.choices,
                                default=Priority.MEDIUM)
    due_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING)
    response = models.TextField(
        blank=True,
        help_text='Written by the assigned teacher when returning the work.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['school', 'status']),
            models.Index(fields=['school', 'assigned_to', 'status']),
        ]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    def save(self, *args, **kwargs):
        if self.school_id is None:
            if self.assigned_to_id and getattr(self.assigned_to, 'school_id', None):
                self.school_id = self.assigned_to.school_id
            elif self.school_class_id and self.school_class.school_id:
                self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        """True while the request is still open and its due date has passed."""
        now = timezone.localdate()
        return (self.status in (
            self.Status.PENDING, self.Status.IN_PROGRESS)
            and self.due_date is not None and self.due_date < now)

    @property
    def is_open(self):
        return self.status in (self.Status.PENDING, self.Status.IN_PROGRESS)


class UpdateRequestEvent(models.Model):
    """One entry in an update request's history (status change or comment)."""

    class Action(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        ASSIGNED = 'ASSIGNED', 'Assigned'
        STARTED = 'STARTED', 'Started'
        RESPONDED = 'RESPONDED', 'Responded'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        COMPLETED = 'COMPLETED', 'Completed'
        REOPENED = 'REOPENED', 'Reopened'
        CANCELLED = 'CANCELLED', 'Cancelled'
        COMMENTED = 'COMMENTED', 'Comment'

    request = models.ForeignKey(
        UpdateRequest, on_delete=models.CASCADE, related_name='events')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='update_request_events')
    action = models.CharField(max_length=12, choices=Action.choices)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.request} - {self.get_action_display()}"


# ---------------------------------------------------------------------------
# Controlled correction of approved / published marks
# ---------------------------------------------------------------------------
class MarkCorrection(SchoolOwnedModel):
    """Audit record for an authorized correction of approved/published marks.

    Correcting approved or published results is a *controlled* operation: a
    reviewer opens a correction with a recorded reason (never silently), the
    responsible teacher edits the marks, and every changed score is captured in
    ``changes`` before the correction is closed when the submission is approved
    again.
    """

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='mark_corrections')
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name='mark_corrections')
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, related_name='mark_corrections')
    submission = models.ForeignKey(
        MarkSubmission, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='corrections',
        help_text='The specific submission reopened (null for a class-level '
                  'unpublish correction).')

    reason = models.TextField(help_text='Why are these results being corrected?')
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='initiated_mark_corrections')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    changes = models.JSONField(
        default=list, blank=True,
        help_text='Before/after snapshots of every score changed while this '
                  'correction was open.')

    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-initiated_at']
        indexes = [models.Index(fields=['school_class', 'academic_year', 'term'])]

    def __str__(self):
        return f"{self.school_class} {self.term}: {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.school_class_id:
            self.school_id = self.school_class.school_id
        super().save(*args, **kwargs)

    @classmethod
    def open_for(cls, academic_year, term, school_class):
        return cls.objects.filter(
            academic_year=academic_year, term=term, school_class=school_class,
            status=cls.Status.OPEN)


# ---------------------------------------------------------------------------
# Exceptional academic-year override
# ---------------------------------------------------------------------------
class AcademicYearOverride(SchoolOwnedModel):
    """An exceptional, audited permission to work in a non-active year.

    A school always has one configured *active* academic year. Occasionally an
    authorized Head Teacher or Admin must temporarily let a specific set of
    teachers capture records against a different year (for example while
    back-filling a previous term). That action is:

    * restricted to :data:`~apps.academics.services.academic_year.OVERRIDE_ROLES`;
    * reason-logged and attributed to the person who enabled it;
    * optionally time-boxed (``expires_at``) and/or scoped to specific teachers
      (``applies_to_all`` / ``teachers``);
    * closed with a timestamp and the user who disabled it, and it never
      bypasses school scoping, assessment approval or any other role rule.

    When the override expires or is disabled, teachers automatically return to
    the school's configured active academic year.
    """

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name='year_overrides',
        help_text='The exceptional year teachers are temporarily allowed to use.')
    term = models.ForeignKey(
        Term, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='year_overrides',
        help_text='Optional term within the override year.')

    reason = models.TextField(help_text='Why is the override required?')
    enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='enabled_year_overrides')
    enabled_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When set, the override stops applying automatically.')

    applies_to_all = models.BooleanField(
        default=True,
        help_text='Apply to every teacher in the school, or only to the '
                  'teachers selected below.')
    teachers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='academic_year_overrides',
        help_text='Teachers the override applies to when not applied to all.')

    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='disabled_year_overrides')
    disabled_at = models.DateTimeField(null=True, blank=True)
    disabled_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-enabled_at']
        indexes = [
            models.Index(fields=['school', 'disabled_at']),
        ]

    def __str__(self):
        state = 'active' if self.is_active else 'closed'
        return f"{self.school} -> {self.academic_year.name} ({state})"

    def save(self, *args, **kwargs):
        if self.school_id is None and self.academic_year_id:
            self.school_id = self.academic_year.school_id
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return (self.disabled_at is None
                and (self.expires_at is None or self.expires_at > timezone.now()))

    @property
    def is_expired(self):
        return (self.disabled_at is None and self.expires_at is not None
                and self.expires_at <= timezone.now())

    def applies_to(self, user):
        if not self.is_active:
            return False
        if self.applies_to_all:
            return True
        return self.teachers.filter(pk=getattr(user, 'pk', None)).exists()
