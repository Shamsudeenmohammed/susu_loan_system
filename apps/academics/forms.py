"""Forms for the academics module.

Forms are school-aware: when a school is active (a school-scoped user, or a
global user who switched into one) the school picker is removed and forced onto
the instance; in the combined view an explicit choice is required. Related
querysets are always narrowed to the schools the request may access.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from apps.school_fees.models import AcademicYear, SchoolClass, Student, Term
from apps.schools.models import School
from apps.schools.scoping import (
    get_active_school,
    scope_queryset,
    scope_reference_queryset,
)

from .models import (
    AcademicCalendarEvent,
    AcademicSettings,
    AcademicYearOverride,
    Announcement,
    Assessment,
    ClassSubject,
    Complaint,
    GradeBand,
    GradingScale,
    PromotionRecord,
    Subject,
    TeacherAssignment,
    TerminalComment,
    TerminalReportField,
    UpdateRequest,
    WeeklyReport,
)

User = get_user_model()


def apply_bootstrap_classes(widget):
    """Add the right Bootstrap 5 class to a widget so fields render styled."""
    existing = widget.attrs.get('class', '')
    if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple,
                           forms.RadioSelect)):
        cls = 'form-check-input'
    elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
        cls = 'form-select'
    else:
        cls = 'form-control'
    if cls not in existing.split():
        widget.attrs['class'] = (existing + ' ' + cls).strip()


class BootstrapMixin:
    """Render every field with the Bootstrap 5 widget classes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            apply_bootstrap_classes(field.widget)


class StyledInlineFormSet(forms.BaseInlineFormSet):
    """Inline formset whose widgets (including DELETE) use Bootstrap classes."""

    def add_fields(self, form, index):
        super().add_fields(form, index)
        for field in form.fields.values():
            apply_bootstrap_classes(field.widget)


# Date formats people commonly type. Listing several lets a single field accept
# ISO (2026-12-10), day-first (10/12/2026) and month-first (12/10/2026) input
# instead of only the ISO format Django defaults to. Day-first is tried before
# month-first so an unambiguous value like 13/10/2026 is read as 13 October.
DATE_INPUT_FORMATS = [
    '%Y-%m-%d',   # 2026-12-10
    '%d/%m/%Y',   # 10/12/2026
    '%d-%m-%Y',   # 10-12-2026
    '%d.%m.%Y',   # 10.12.2026
    '%d/%m/%y',   # 10/12/26
    '%m/%d/%Y',   # 12/10/2026
    '%m-%d-%Y',   # 12-10-2026
    '%d %b %Y',   # 10 Dec 2026
    '%d %B %Y',   # 10 December 2026
    '%b %d, %Y',  # Dec 10, 2026
]

# Shown as the placeholder so users can see the accepted format before typing.
DATE_PLACEHOLDER = 'YYYY-MM-DD or DD/MM/YYYY (e.g. 2026-12-10)'


def flexible_date_field(**kwargs):
    """A DateField that accepts the common formats above and hints the format."""
    attrs = {'class': 'form-control', 'placeholder': DATE_PLACEHOLDER}
    attrs.update(kwargs.pop('widget_attrs', {}))
    return forms.DateField(
        input_formats=DATE_INPUT_FORMATS,
        widget=forms.DateInput(attrs=attrs),
        **kwargs,
    )


class BaseRequestForm(BootstrapMixin, forms.ModelForm):
    """ModelForm that receives the request and applies school scoping."""

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request is not None:
            self._scope()

    def _scope(self):  # pragma: no cover - overridden
        pass

    def _scope_school_field(self):
        """Handle the model's ``school`` field for the active view."""
        if 'school' not in self.fields:
            return get_active_school(self.request)
        active = get_active_school(self.request)
        if active is not None:
            self.fields.pop('school')
            self.instance.school = active
            return active
        self.fields['school'].queryset = School.objects.filter(is_active=True)
        self.fields['school'].required = True
        return None

    def _scope_owned(self, qs):
        if self.request is None:
            return qs
        return scope_queryset(qs, self.request)

    def _scope_shared(self, qs):
        """Shared reference data (years/terms) also visible to branch users."""
        if self.request is None:
            return qs
        return scope_reference_queryset(qs, self.request)


class SubjectForm(BaseRequestForm):
    class Meta:
        model = Subject
        fields = ['school', 'name', 'code', 'is_core', 'is_active']

    def _scope(self):
        self._scope_school_field()


class ClassSubjectForm(BaseRequestForm):
    class Meta:
        model = ClassSubject
        fields = ['school_class', 'subject', 'is_active']

    def _scope(self):
        self.fields['school_class'].queryset = self._scope_owned(
            SchoolClass.objects.filter(is_active=True))
        self.fields['subject'].queryset = self._scope_owned(
            Subject.objects.filter(is_active=True))
        active = get_active_school(self.request)
        if active is not None:
            self.fields['school_class'].queryset = self.fields[
                'school_class'].queryset.filter(school=active)
            self.fields['subject'].queryset = self.fields[
                'subject'].queryset.filter(school=active)


class TeacherAssignmentForm(BaseRequestForm):
    class Meta:
        model = TeacherAssignment
        fields = ['teacher', 'school_class', 'subject', 'academic_year',
                  'term', 'is_active']

    def _scope(self):
        active = get_active_school(self.request)
        teachers = User.objects.filter(role='TEACHER', is_active=True)
        classes = self._scope_owned(SchoolClass.objects.filter(is_active=True))
        subjects = self._scope_owned(Subject.objects.filter(is_active=True))
        years = self._scope_shared(AcademicYear.objects.all())
        if active is not None:
            teachers = teachers.filter(school=active)
            classes = classes.filter(school=active)
            subjects = subjects.filter(school=active)
        self.fields['teacher'].queryset = teachers.order_by('first_name', 'last_name')
        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['subject'].queryset = subjects.order_by('name')
        self.fields['academic_year'].queryset = years.order_by('-name')
        self.fields['term'].queryset = Term.objects.filter(
            academic_year__in=years).order_by('academic_year', 'term_number')
        # Restrict subjects to those offered by the selected class when known.
        class_id = self._selected_class_id()
        if class_id:
            offered = ClassSubject.objects.filter(
                school_class_id=class_id, is_active=True
            ).values_list('subject_id', flat=True)
            self.fields['subject'].queryset = self.fields[
                'subject'].queryset.filter(pk__in=list(offered))

    def _selected_class_id(self):
        if self.is_bound:
            return self.data.get('school_class') or None
        if self.instance and self.instance.pk:
            return self.instance.school_class_id
        return None


class GradingScaleForm(BaseRequestForm):
    class Meta:
        model = GradingScale
        fields = ['school', 'name', 'is_default', 'is_active']

    def _scope(self):
        self._scope_school_field()

    def clean_is_default(self):
        make_default = self.cleaned_data.get('is_default')
        if make_default:
            school = self.instance.school or get_active_school(self.request)
            qs = GradingScale.objects.filter(school=school, is_default=True)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            qs.update(is_default=False)
        return make_default


GradeBandFormSet = inlineformset_factory(
    GradingScale,
    GradeBand,
    fields=['grade', 'description', 'min_score', 'max_score', 'remark', 'order'],
    extra=5,
    can_delete=True,
    formset=StyledInlineFormSet,
)


class AcademicSettingsForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AcademicSettings
        fields = [
            'default_grading_scale', 'pass_mark', 'promotion_min_average',
            'continuous_assessment_weight', 'exam_weight', 'show_position',
            'show_attendance_on_report', 'show_financials_on_report',
            'require_complete_results_for_publication',
            'require_attendance_for_publication',
            'currency',
        ]

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        if school is not None:
            self.instance.school = school
            self.fields['default_grading_scale'].queryset = GradingScale.objects.filter(
                school=school, is_active=True)

    def clean(self):
        cleaned = super().clean()
        ca = cleaned.get('continuous_assessment_weight') or 0
        exam = cleaned.get('exam_weight') or 0
        if ca and exam and (ca + exam) != 100:
            self.add_error(
                'exam_weight',
                f'Continuous assessment and exam weights should add up to 100 '
                f'(currently {ca + exam}).')
        return cleaned


class AssessmentForm(BaseRequestForm):
    # Override the model-generated date fields so they accept the range of
    # formats users type and show the expected format as a placeholder.
    date = flexible_date_field(required=False)
    marks_due_date = flexible_date_field(required=False)

    class Meta:
        model = Assessment
        fields = ['academic_year', 'term', 'school_class', 'subject', 'name',
                  'assessment_type', 'max_score', 'weight', 'date',
                  'marks_due_date', 'is_active']

    def __init__(self, *args, context=None, **kwargs):
        # ``context`` is the resolved active academic year for the request. For
        # a teacher it is authoritative: the year/term fields are removed and
        # forced on the instance so a teacher can never submit another year.
        self.context = context
        super().__init__(*args, **kwargs)

    def _scope(self):
        active = get_active_school(self.request)
        years = self._scope_shared(AcademicYear.objects.all())
        classes = self._scope_owned(SchoolClass.objects.filter(is_active=True))
        subjects = self._scope_owned(Subject.objects.filter(is_active=True))
        if active is not None:
            classes = classes.filter(school=active)
            subjects = subjects.filter(school=active)
        self.fields['academic_year'].queryset = years.order_by('-name')
        self.fields['term'].queryset = Term.objects.filter(
            academic_year__in=years).order_by('academic_year', 'term_number')
        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['subject'].queryset = subjects.order_by('name')

        # A teacher may only configure assessments for their own assignments.
        from .services import access
        if access.is_teacher_only(self.request.user):
            pairs = access.teacher_class_subject_pairs(self.request.user)
            class_ids = list(pairs.keys())
            self.fields['school_class'].queryset = self.fields[
                'school_class'].queryset.filter(pk__in=class_ids)
            class_id = self._selected_class_id()
            subject_ids = pairs.get(int(class_id), set()) if class_id else set().union(
                *pairs.values()) if pairs else set()
            self.fields['subject'].queryset = self.fields[
                'subject'].queryset.filter(pk__in=list(subject_ids))
            self._lock_year_to_active()

        class_id = self._selected_class_id()
        if class_id:
            offered = ClassSubject.objects.filter(
                school_class_id=class_id, is_active=True
            ).values_list('subject_id', flat=True)
            self.fields['subject'].queryset = self.fields[
                'subject'].queryset.filter(pk__in=list(offered))

    def _lock_year_to_active(self):
        """Force the resolved active year/term and hide the pickers (teachers)."""
        if self.context is None or self.context.year is None:
            return
        self.fields.pop('academic_year', None)
        self.fields.pop('term', None)
        self.instance.academic_year = self.context.year
        if self.context.term is not None:
            self.instance.term = self.context.term

    def _selected_class_id(self):
        if self.is_bound:
            return self.data.get('school_class') or None
        if self.instance and self.instance.pk:
            return self.instance.school_class_id
        return None

    def clean(self):
        cleaned = super().clean()
        school_class = cleaned.get('school_class')
        subject = cleaned.get('subject')
        if school_class and subject and school_class.school_id != subject.school_id:
            raise forms.ValidationError(
                'The class and subject must belong to the same school.')
        max_score = cleaned.get('max_score')
        if max_score is not None and max_score <= 0:
            self.add_error('max_score', 'Maximum score must be greater than zero.')
        return cleaned


class AttendanceEntryForm(forms.Form):
    """Not a ModelForm: the view builds one row per student."""


class TerminalCommentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TerminalComment
        fields = ['class_teacher_comment', 'head_teacher_remark', 'promotion_status']


class PromotionForm(BootstrapMixin, forms.Form):
    decision = forms.ChoiceField(choices=PromotionRecord.Decision.choices)
    to_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.none(), required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    def __init__(self, *args, school=None, current_class=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = SchoolClass.objects.filter(is_active=True)
        if school is not None:
            qs = qs.filter(school=school)
        if current_class is not None:
            qs = qs.exclude(pk=current_class.pk)
        self.fields['to_class'].queryset = qs.order_by('name')

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get('decision')
        if decision == PromotionRecord.Decision.PROMOTED and not cleaned.get('to_class'):
            self.add_error('to_class', 'Choose the class to promote the student into.')
        return cleaned


class AcademicCalendarEventForm(BaseRequestForm):
    class Meta:
        model = AcademicCalendarEvent
        fields = ['school', 'title', 'event_type', 'start_date', 'end_date',
                  'description', 'is_all_day']

    start_date = flexible_date_field()
    end_date = flexible_date_field(required=False)

    def _scope(self):
        self._scope_school_field()

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'End date cannot be before the start date.')
        return cleaned


class AnnouncementForm(BaseRequestForm):
    class Meta:
        model = Announcement
        fields = ['school', 'title', 'body', 'audience', 'is_published']

    def _scope(self):
        self._scope_school_field()


TerminalReportFieldFormSet = inlineformset_factory(
    School,
    TerminalReportField,
    fields=['key', 'label', 'display_order', 'is_visible'],
    extra=0,
    can_delete=False,
    formset=StyledInlineFormSet,
)


class UpdateRequestForm(BaseRequestForm):
    """Create a Head Teacher update request for a responsive teacher.

    Every related field is school-scoped on the server. When the school is
    active the picker is removed and locked to that school; in the combined
    view an explicit choice is required. Cross-school selections are rejected
    in :meth:`clean` so a request can never leak outside its school.
    """

    due_date = flexible_date_field(required=False)

    class Meta:
        model = UpdateRequest
        fields = ['school', 'title', 'category', 'priority', 'description',
                  'assigned_to', 'academic_year', 'term', 'school_class',
                  'subject', 'student', 'assessment', 'due_date']

    def _scope(self):
        self._scope_school_field()
        active = get_active_school(self.request)

        teachers = User.objects.filter(role='TEACHER', is_active=True)
        classes = self._scope_owned(SchoolClass.objects.filter(is_active=True))
        subjects = self._scope_owned(Subject.objects.filter(is_active=True))
        students = self._scope_owned(Student.objects.filter(is_active=True))
        assessments = self._scope_owned(Assessment.objects.filter(is_active=True))
        years = self._scope_shared(AcademicYear.objects.all())

        if active is not None:
            teachers = teachers.filter(school=active)
            classes = classes.filter(school=active)
            subjects = subjects.filter(school=active)
            students = students.filter(school=active)
            assessments = assessments.filter(school=active)

        self.fields['assigned_to'].queryset = teachers.order_by('first_name', 'last_name')
        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['subject'].queryset = subjects.order_by('name')
        self.fields['student'].queryset = students.order_by('last_name', 'first_name')
        self.fields['assessment'].queryset = assessments.select_related(
            'school_class', 'subject').order_by('name')
        self.fields['academic_year'].queryset = years.order_by('-name')
        self.fields['term'].queryset = Term.objects.filter(
            academic_year__in=years).order_by('academic_year', 'term_number')

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school') or get_active_school(self.request)
        assigned_to = cleaned.get('assigned_to')
        school_class = cleaned.get('school_class')
        subject = cleaned.get('subject')
        student = cleaned.get('student')
        assessment = cleaned.get('assessment')
        year = cleaned.get('academic_year')
        term = cleaned.get('term')

        if school is not None:
            for label, obj in (('assigned_to', assigned_to),
                               ('school_class', school_class),
                               ('subject', subject),
                               ('student', student),
                               ('assessment', assessment)):
                if obj is None:
                    continue
                obj_school = getattr(obj, 'school_id', None)
                if obj_school is not None and obj_school != school.pk:
                    self.add_error(
                        label,
                        f'The {label.replace("_", " ")} must belong to '
                        f'{getattr(school, "display_name", school)}.')

        if school_class and subject and school_class.school_id != subject.school_id:
            self.add_error(
                'subject',
                'The class and subject must belong to the same school.')
        if school_class and student and student.school_class_id != school_class.pk:
            self.add_error(
                'student', 'The student is not enrolled in the selected class.')
        if assessment:
            if school_class and assessment.school_class_id != school_class.pk:
                self.add_error(
                    'assessment', 'The assessment does not belong to the selected class.')
            if subject and assessment.subject_id != subject.pk:
                self.add_error(
                    'assessment', 'The assessment does not match the selected subject.')
            if year and assessment.academic_year_id != year.pk:
                self.add_error(
                    'assessment', 'The assessment does not match the selected academic year.')
            if term and assessment.term_id != term.pk:
                self.add_error(
                    'assessment', 'The assessment does not match the selected term.')
        return cleaned


class UpdateRequestCommentForm(BootstrapMixin, forms.Form):
    """Append a comment to a request's history."""

    comment = forms.CharField(
        label='Add a comment', widget=forms.Textarea(attrs={'rows': 2}))


class UpdateRequestResponseForm(BootstrapMixin, forms.Form):
    """Text accompanying a status transition (teacher or manager side)."""

    notes = forms.CharField(
        label='Response / notes', required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Summarise what was done…'}))


class MarkCorrectionReasonForm(BootstrapMixin, forms.Form):
    """Record the reason required by the controlled-correction workflow."""

    reason = forms.CharField(
        label='Reason', widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Why are these approved/published results being reopened?',
        }))


class AcademicYearOverrideForm(BootstrapMixin, forms.ModelForm):
    """Authorized Head Teacher / Admin control for an exceptional override.

    The academic year list is limited to the selected school, the teacher list
    to that school's teachers, and the term to the chosen year so a reviewer
    can only ever configure a coherent, in-school override.
    """

    class Meta:
        model = AcademicYearOverride
        fields = ['academic_year', 'term', 'reason', 'expires_at',
                  'applies_to_all', 'teachers']

    def __init__(self, *args, request=None, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.school = school

        years = AcademicYear.objects.none()
        if school is not None:
            years = AcademicYear.objects.filter(school=school).order_by('-name')
        self.fields['academic_year'].queryset = years
        self.fields['term'].queryset = Term.objects.filter(
            academic_year__in=years).order_by('academic_year', 'term_number')

        teachers = User.objects.filter(role='TEACHER', is_active=True)
        if school is not None:
            teachers = teachers.filter(school=school)
        self.fields['teachers'].queryset = teachers.order_by(
            'first_name', 'last_name')
        self.fields['teachers'].required = False
        self.fields['teachers'].help_text = (
            'Only used when the override does not apply to all teachers.')

        self.fields['expires_at'].required = False
        self.fields['expires_at'].widget = forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': 'form-control'},
            format='%Y-%m-%dT%H:%M')
        self.fields['expires_at'].input_formats = (
            '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M')

    def clean(self):
        cleaned = super().clean()
        year = cleaned.get('academic_year')
        term = cleaned.get('term')
        if term is not None and year is not None and term.academic_year_id != year.pk:
            self.add_error(
                'term', 'The term must belong to the selected academic year.')
        if not cleaned.get('applies_to_all') and not cleaned.get('teachers'):
            self.add_error(
                'teachers',
                'Select at least one teacher, or choose to apply to all teachers.')
        return cleaned


# ---------------------------------------------------------------------------
# Weekly teacher reports
# ---------------------------------------------------------------------------
class WeeklyReportForm(BaseRequestForm):
    """Create or revise a teacher's weekly report draft.

    A teacher-only user is pinned to their own assignments and active academic
    year (the pickers are removed); a reviewer may create/revise on behalf of a
    teacher and choose the year. Cross-school selections are rejected.
    """

    week_start = flexible_date_field()

    class Meta:
        model = WeeklyReport
        fields = ['school', 'teacher', 'school_class', 'subject', 'academic_year',
                  'term', 'week_start', 'summary', 'challenges', 'support_needed',
                  'next_week_plan', 'attendance_notes']

    def _scope(self):
        from .services import access

        active = self._scope_school_field()
        teachers = User.objects.filter(role='TEACHER', is_active=True)
        classes = self._scope_owned(SchoolClass.objects.filter(is_active=True))
        subjects = self._scope_owned(Subject.objects.filter(is_active=True))
        years = self._scope_shared(AcademicYear.objects.all())
        if active is not None:
            teachers = teachers.filter(school=active)
            classes = classes.filter(school=active)
            subjects = subjects.filter(school=active)

        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['subject'].queryset = subjects.order_by('name')
        self.fields['subject'].required = False
        self.fields['academic_year'].queryset = years.order_by('-name')
        self.fields['term'].queryset = Term.objects.filter(
            academic_year__in=years).order_by('academic_year', 'term_number')

        if access.is_teacher_only(self.request.user):
            teacher = self.request.user
            self.fields.pop('teacher', None)
            self.instance.teacher = teacher
            if getattr(teacher, 'school_id', None):
                classes = classes.filter(school_id=teacher.school_id)
                subjects = subjects.filter(school_id=teacher.school_id)
            pairs = access.teacher_class_subject_pairs(teacher)
            if pairs:
                classes = classes.filter(pk__in=list(pairs.keys()))
            # Re-narrow after the teacher's assignments are known.
            self.fields['school_class'].queryset = classes.order_by('name')
            self.fields['subject'].queryset = subjects.order_by('name')
            self._lock_year_to_active()
        else:
            self.fields['teacher'].queryset = teachers.order_by(
                'first_name', 'last_name')

    def _lock_year_to_active(self):
        from .services import academic_year as academic_year_service

        context = academic_year_service.build_context(self.request)
        if context.year is None:
            return
        self.fields.pop('academic_year', None)
        self.fields.pop('term', None)
        self.instance.academic_year = context.year
        if context.term is not None:
            self.instance.term = context.term

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school') or get_active_school(self.request)
        teacher = cleaned.get('teacher') or getattr(self.instance, 'teacher', None)
        school_class = cleaned.get('school_class')
        subject = cleaned.get('subject')
        if school is not None and school_class is not None \
                and school_class.school_id != school.pk:
            self.add_error('school_class', 'Choose a class from your school.')
        if teacher is not None and school_class is not None \
                and getattr(teacher, 'school_id', None) != school_class.school_id:
            self.add_error(
                'school_class', 'The class must belong to the same school as the teacher.')
        if subject is not None and school_class is not None \
                and subject.school_id != school_class.school_id:
            self.add_error(
                'subject', 'The class and subject must belong to the same school.')
        return cleaned


class WeeklyReportReviewForm(BootstrapMixin, forms.Form):
    """Reviewer comment accompanying an acknowledge/clarify/accept action."""

    comment = forms.CharField(
        label='Comment', required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Notes for the teacher (required when requesting clarification)…'}))


class WeeklyReportCommentForm(BootstrapMixin, forms.Form):
    comment = forms.CharField(
        label='Add a comment',
        widget=forms.Textarea(attrs={'rows': 2}))


# ---------------------------------------------------------------------------
# Complaints, requests and safeguarding
# ---------------------------------------------------------------------------
class ComplaintForm(BaseRequestForm):
    """Raise a complaint, request or safeguarding concern."""

    due_date = flexible_date_field(required=False)

    class Meta:
        model = Complaint
        fields = ['school', 'case_type', 'category', 'title', 'description',
                  'supporting_details', 'priority', 'school_class', 'student',
                  'assigned_to', 'due_date']

    def _scope(self):
        self._scope_school_field()
        active = get_active_school(self.request)

        classes = self._scope_owned(SchoolClass.objects.filter(is_active=True))
        students = self._scope_owned(Student.objects.filter(is_active=True))
        assignees = User.objects.filter(
            is_active=True,
            role__in=['SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER', 'MANAGER'])
        if active is not None:
            classes = classes.filter(school=active)
            students = students.filter(school=active)
            assignees = assignees.filter(school=active)

        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['student'].queryset = students.order_by('last_name', 'first_name')
        self.fields['assigned_to'].queryset = assignees.order_by(
            'first_name', 'last_name')
        self.fields['assigned_to'].required = False

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school') or get_active_school(self.request)
        school_class = cleaned.get('school_class')
        student = cleaned.get('student')
        assignee = cleaned.get('assigned_to')
        case_type = cleaned.get('case_type')
        priority = cleaned.get('priority')

        if school is not None:
            for label, obj in (('school_class', school_class),
                               ('student', student),
                               ('assigned_to', assignee)):
                if obj is None:
                    continue
                obj_school = getattr(obj, 'school_id', None)
                if obj_school is not None and obj_school != school.pk:
                    self.add_error(label, 'That selection belongs to another school.')
        if school_class is not None and student is not None \
                and student.school_class_id != school_class.pk:
            self.add_error(
                'student', 'The student is not enrolled in the selected class.')

        escalated = (case_type == Complaint.CaseType.SAFEGUARDING
                     or priority == Complaint.Priority.URGENT)
        if escalated and not (cleaned.get('supporting_details') or '').strip():
            self.add_error(
                'supporting_details',
                'Please give supporting details for a safeguarding or urgent case.')
        return cleaned


class ComplaintCommentForm(BootstrapMixin, forms.Form):
    comment = forms.CharField(
        label='Comment / response',
        widget=forms.Textarea(attrs={'rows': 3}))


class ComplaintTransitionForm(BootstrapMixin, forms.Form):
    notes = forms.CharField(
        label='Notes', required=False,
        widget=forms.Textarea(attrs={
            'rows': 3, 'placeholder': 'Optional notes for this action…'}))


class ComplaintAssignForm(BootstrapMixin, forms.Form):
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False,
        label='Assign to')

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        assignees = User.objects.filter(
            is_active=True,
            role__in=['SUPER_ADMIN', 'ADMIN', 'PROPRIETOR', 'HEAD_TEACHER', 'MANAGER'])
        if school is not None:
            assignees = assignees.filter(school=school)
        self.fields['assigned_to'].queryset = assignees.order_by(
            'first_name', 'last_name')


# ---------------------------------------------------------------------------
# Official terminal reports
# ---------------------------------------------------------------------------
class TerminalReportBulkForm(BootstrapMixin, forms.Form):
    """Choose a class/period and (optionally) the students to generate."""

    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.none(), label='Class')
    students = forms.ModelMultipleChoiceField(
        queryset=Student.objects.none(), required=False,
        label='Students (leave blank for the whole class)')
    force = forms.BooleanField(
        required=False, label='Generate despite incomplete data',
        help_text='Only for an authorised reason; the override is recorded.')
    override_reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}),
        label='Override reason',
        help_text='Required when generating despite incomplete data.')

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        classes = SchoolClass.objects.filter(is_active=True)
        students = Student.objects.filter(is_active=True)
        if school is not None:
            classes = classes.filter(school=school)
            students = students.filter(school=school)
        self.fields['school_class'].queryset = classes.order_by('name')
        self.fields['students'].queryset = students.order_by(
            'last_name', 'first_name')


class TerminalReportApprovalForm(BootstrapMixin, forms.Form):
    comment = forms.CharField(
        label='Approval comment', required=False,
        widget=forms.Textarea(attrs={'rows': 2}))


class TerminalReportPublishForm(BootstrapMixin, forms.Form):
    force = forms.BooleanField(
        required=False, label='Publish despite incomplete data')
    override_reason = forms.CharField(
        label='Override reason', required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Required when publishing despite incomplete data.')


class TerminalReportAmendmentForm(BootstrapMixin, forms.Form):
    reason = forms.CharField(
        label='Reason for amendment',
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Why is the official report being corrected?'}))
