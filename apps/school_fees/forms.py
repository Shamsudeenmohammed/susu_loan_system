from django import forms
from django.core.validators import MinValueValidator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal

from apps.core.utils import normalize_ghana_phone
from apps.schools.models import School
from apps.schools.scoping import with_shared

from .models import (
    SchoolClass,
    AcademicYear,
    Term,
    Student,
    FeeCategory,
    FeeStructure,
    StudentFeeAccount,
    FeePayment,
    ReminderTemplate,
)
from .services import accounts


def _scope_school_field(form, school, field_name='school'):
    """Configure a school-selecting field shared by setup forms.

    * When a school is active (a school-scoped user, or a global user who has
      switched into one) the picker is removed and the instance's school is
      forced to that school, so it can never be changed from the request.
    * When viewing all schools (combined) the picker is shown and required, so
      an administrator must explicitly choose the branch before saving.

    Records created before multi-school (and the existing test fixtures) may
    still have no school. They remain visible to global users; views hide such
    records from school-scoped users so data never leaks between schools.
    """
    if school is not None:
        form.fields.pop(field_name, None)
        form.instance.school = school
    else:
        f = form.fields.get(field_name)
        if f is not None:
            f.queryset = School.objects.filter(is_active=True)
            f.required = True
            f.label = 'School'
            f.help_text = (
                'Required while viewing all schools. A branch head teacher '
                'only ever sees their own school.'
            )
            f.empty_label = '\u2014 Select a school \u2014'


def _posted_school(form):
    """The school selected on a bound form in the combined view (or None)."""
    if getattr(form, 'is_bound', False):
        sid = form.data.get('school')
        if sid:
            return School.objects.filter(pk=sid, is_active=True).first()
    return None


def _adopt_school(school, *records):
    """Attach legacy (school-less) records to ``school`` on first explicit use.

    Only records that currently have no school are touched; anything already
    owned by a school is left alone, so branches never cross-contaminate. A
    uniqueness clash (a record with the same code/name already exists in the
    school) is ignored so the caller's save still succeeds.
    """
    if school is None:
        return
    for record in records:
        if record is None:
            continue
        target = record.academic_year if isinstance(record, Term) else record
        if getattr(target, 'school_id', None) is None:
            try:
                with transaction.atomic():
                    target.school = school
                    target.save(update_fields=['school'])
            except IntegrityError:
                pass


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ['school', 'name', 'code', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['school'].widget.attrs.update({'class': 'form-select'})
        _scope_school_field(self, school)


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ['school', 'name', 'start_date', 'end_date', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['school'].widget.attrs.update({'class': 'form-select'})
        _scope_school_field(self, school)


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ['academic_year', 'name', 'term_number', 'start_date', 'end_date']
        widgets = {
            'academic_year': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'term_number': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        years = AcademicYear.objects.all()
        if school is not None:
            years = with_shared(years, school)
        self.fields['academic_year'].queryset = years
        if not self.fields['academic_year'].initial:
            ay = years.filter(is_active=True).first()
            if ay:
                self.fields['academic_year'].initial = ay.pk


class FeeCategoryForm(forms.ModelForm):
    class Meta:
        model = FeeCategory
        fields = ['school', 'name', 'code', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['school'].widget.attrs.update({'class': 'form-select'})
        _scope_school_field(self, school)


class FeeStructureForm(forms.ModelForm):
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}))
    term = forms.ModelChoiceField(
        queryset=Term.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}))
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}))
    fee_category = forms.ModelChoiceField(
        queryset=FeeCategory.objects.filter(is_active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control'}))
    due_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    class Meta:
        model = FeeStructure
        fields = ['school', 'academic_year', 'term', 'school_class', 'fee_category',
                  'amount', 'due_date', 'description', 'is_active']

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_active'].widget.attrs.update({'class': 'form-check-input'})
        chosen = school or _posted_school(self)
        _scope_school_field(self, school)
        if chosen is None and getattr(self.instance, 'school_id', None):
            chosen = self.instance.school
        if chosen is not None:
            if school is not None:
                # A school-scoped user only ever sees their own branch, plus the
                # organisation-wide (shared) academic calendar and categories.
                self.fields['academic_year'].queryset = with_shared(
                    AcademicYear.objects.all(), chosen)
                self.fields['term'].queryset = with_shared(
                    Term.objects.all(), chosen, field='academic_year__school')
                self.fields['school_class'].queryset = SchoolClass.objects.filter(school=chosen)
                self.fields['fee_category'].queryset = with_shared(
                    FeeCategory.objects.filter(is_active=True), chosen)
            else:
                # A global admin in the combined view also sees legacy
                # (school-less) records so they stay selectable; they are
                # adopted into the chosen school on save.
                self.fields['academic_year'].queryset = AcademicYear.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True))
                self.fields['term'].queryset = Term.objects.filter(
                    Q(academic_year__school=chosen) | Q(academic_year__school__isnull=True))
                self.fields['school_class'].queryset = SchoolClass.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True))
                self.fields['fee_category'].queryset = FeeCategory.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True), is_active=True)
        # Keep the current selections selectable while editing legacy records.
        if self.instance is not None and self.instance.pk:
            for field, current_id in (
                    ('academic_year', self.instance.academic_year_id),
                    ('term', self.instance.term_id),
                    ('school_class', self.instance.school_class_id),
                    ('fee_category', self.instance.fee_category_id)):
                base = self.fields[field].queryset
                self.fields[field].queryset = base.model.objects.filter(
                    Q(pk__in=base.values('pk')) | Q(pk=current_id))

    def save(self, commit=True):
        structure = super().save(commit=False)
        school = structure.school or self.cleaned_data.get('school')
        _adopt_school(school, structure.academic_year, structure.school_class,
                      structure.fee_category, structure.term)
        if commit:
            structure.save()
        return structure

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        return amount

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school') or getattr(self.instance, 'school', None)
        ay = cleaned.get('academic_year')
        term = cleaned.get('term')
        if ay and term and ay.pk != term.academic_year_id:
            self.add_error('term', 'Selected term does not belong to the selected academic year.')
        if school is not None:
            term_school = getattr(getattr(term, 'academic_year', None), 'school_id', None)
            selections = {
                'academic_year': getattr(ay, 'school_id', None),
                'term': term_school,
                'school_class': getattr(cleaned.get('school_class'), 'school_id', None),
                'fee_category': getattr(cleaned.get('fee_category'), 'school_id', None),
            }
            for name, sid in selections.items():
                # Legacy (school-less) selections are allowed; they are adopted
                # into the chosen school on save.
                if cleaned.get(name) is not None and sid not in (None, school.pk):
                    self.add_error(
                        name,
                        'This selection belongs to a different school than the one chosen.')
        return cleaned


class StudentForm(forms.ModelForm):
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    parent_phone = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 0241234567'}))

    class Meta:
        model = Student
        fields = ['school', 'first_name', 'last_name', 'school_class', 'parent_name',
                  'parent_phone', 'parent_email', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['school'].widget.attrs.update({'class': 'form-select'})
        chosen = school or _posted_school(self)
        _scope_school_field(self, school)
        if chosen is None and getattr(self.instance, 'school_id', None):
            chosen = self.instance.school
        classes = SchoolClass.objects.filter(is_active=True)
        if chosen is not None:
            if school is not None:
                # A school-scoped user only ever sees their own branch.
                classes = classes.filter(school=chosen)
            else:
                # A global admin in the combined view also sees legacy
                # (school-less) classes so they remain selectable; they are
                # adopted into the chosen school on save.
                classes = classes.filter(Q(school=chosen) | Q(school__isnull=True))
        # Keep the student's current class selectable while editing, even when
        # it is inactive or not yet assigned to the selected school.
        current_class_id = getattr(self.instance, 'school_class_id', None)
        if current_class_id:
            classes = SchoolClass.objects.filter(
                Q(pk__in=classes.values('pk')) | Q(pk=current_class_id))
        self.fields['school_class'].queryset = classes.order_by('name')

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school') or getattr(self.instance, 'school', None)
        school_class = cleaned.get('school_class')
        # Only reject a class that belongs to a *different* school. Legacy
        # classes with no school yet are allowed; they are adopted on save.
        if (school is not None and school_class is not None
                and school_class.school_id not in (None, school.pk)):
            self.add_error(
                'school_class',
                f'"{school_class.name}" does not belong to {school.name}. '
                'Choose a class in the selected school, or assign the class '
                'to that school first.')
        return cleaned

    def save(self, commit=True):
        student = super().save(commit=False)
        school = student.school or self.cleaned_data.get('school')
        school_class = student.school_class
        if school is not None and school_class is not None and school_class.school_id is None:
            try:
                with transaction.atomic():
                    school_class.school = school
                    school_class.save(update_fields=['school'])
            except IntegrityError:
                # A class with the same code already exists in this school;
                # leave the legacy class unassigned rather than fail the save.
                pass
        if commit:
            student.save()
        return student

    def clean_parent_phone(self):
        phone = self.cleaned_data.get('parent_phone')
        return normalize_ghana_phone(phone) if phone else phone


class FeePaymentForm(forms.Form):
    """Staff-recorded payment for an existing fee account."""

    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    payment_date = forms.DateField(
        initial=timezone.now, widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    payment_method = forms.ChoiceField(
        choices=FeePayment.PaymentMethod.choices,
        widget=forms.Select(attrs={'class': 'form-select'}))
    reference = forms.CharField(
        required=False, widget=forms.TextInput(attrs={'class': 'form-control'}))
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        return amount


class FeeAccountForm(forms.ModelForm):
    """Admin can adjust the total fees on an account if needed."""

    class Meta:
        model = StudentFeeAccount
        fields = ['total_fees']
        widgets = {
            'total_fees': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def clean_total_fees(self):
        total = self.cleaned_data.get('total_fees')
        if total is not None and total < 0:
            raise forms.ValidationError('Total fees cannot be negative.')
        return total


class StudentFeeAccountAdminForm(forms.ModelForm):
    """Add/edit form for a student fee account.

    The add form lets the admin either pick one specific student (search by
    name) or tick "Apply to all students" to create an account for every active
    student for the selected academic year + term at once.
    """

    apply_to_all = forms.BooleanField(
        required=False,
        label='Apply to all students',
        help_text=(
            'Tick to create a fee account for every active student for the '
            'selected academic year and term. Leave unticked to create one for '
            'a single student (search by name below).'
        ),
    )

    class Meta:
        model = StudentFeeAccount
        fields = [
            'apply_to_all', 'student', 'academic_year', 'term',
            'total_fees', 'amount_paid', 'status', 'last_payment_date',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['student'].required = False
        self.fields['student'].help_text = (
            'Search by student name, or tick "Apply to all students" instead.'
        )

    def clean(self):
        cleaned = super().clean()
        apply_all = cleaned.get('apply_to_all')
        student = cleaned.get('student')
        if apply_all and student:
            raise forms.ValidationError(
                'Choose either "Apply to all students" or a specific student, not both.'
            )
        if not apply_all and not student:
            self.add_error('student', 'Select a student or tick "Apply to all students".')
        if apply_all and (not cleaned.get('academic_year') or not cleaned.get('term')):
            raise forms.ValidationError(
                'An academic year and term are required when applying to all students.'
            )
        ay = cleaned.get('academic_year')
        term = cleaned.get('term')
        if ay and term and term.academic_year_id != ay.pk:
            raise forms.ValidationError(
                'The selected term does not belong to the selected academic year.'
            )
        return cleaned


class AssignFeeForm(forms.Form):
    """Assign a fee to all students, specific classes, or a single student.

    The admin picks an academic year, term, fee category, amount and due date,
    then chooses the scope: all students (every class), specific classes
    (one or more), or a single student (search by name / ID).
    """

    SCOPE_ALL = 'all'
    SCOPE_CLASSES = 'classes'
    SCOPE_SINGLE = 'single'
    SCOPE_CHOICES = [
        (SCOPE_ALL, 'All Students (every class)'),
        (SCOPE_CLASSES, 'Specific Classes (one or more)'),
        (SCOPE_SINGLE, 'Single Student'),
    ]

    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}))
    term = forms.ModelChoiceField(
        queryset=Term.objects.all(), widget=forms.Select(attrs={'class': 'form-select'}))
    fee_category = forms.ModelChoiceField(
        queryset=FeeCategory.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    due_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    scope = forms.ChoiceField(
        choices=SCOPE_CHOICES,
        initial=SCOPE_ALL,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}))

    classes = forms.ModelMultipleChoiceField(
        queryset=SchoolClass.objects.filter(is_active=True),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '8'}))

    student = forms.ModelChoiceField(
        queryset=Student.objects.filter(is_active=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    school = forms.ModelChoiceField(
        queryset=School.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        chosen = school or _posted_school(self)
        if school is not None:
            self.fields.pop('school', None)
        else:
            self.fields['school'].queryset = School.objects.filter(is_active=True)
            self.fields['school'].required = True
            self.fields['school'].label = 'School'
            self.fields['school'].empty_label = 'Select a school'
            self.fields['school'].help_text = (
                'Required while viewing all schools. A branch head teacher only '
                'ever sees their own school.'
            )
        if chosen is not None:
            if school is not None:
                # A school-scoped user only ever sees their own branch, plus the
                # organisation-wide (shared) academic calendar and categories.
                self.fields['academic_year'].queryset = with_shared(
                    AcademicYear.objects.all(), chosen)
                self.fields['term'].queryset = with_shared(
                    Term.objects.all(), chosen, field='academic_year__school')
                self.fields['fee_category'].queryset = with_shared(
                    FeeCategory.objects.filter(is_active=True), chosen)
                self.fields['classes'].queryset = SchoolClass.objects.filter(
                    is_active=True, school=chosen)
                self.fields['student'].queryset = Student.objects.filter(
                    is_active=True, school=chosen)
            else:
                # A global admin in the combined view also sees legacy
                # (school-less) records so they stay selectable; they are
                # adopted into the chosen school when the fee is assigned.
                self.fields['academic_year'].queryset = AcademicYear.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True))
                self.fields['term'].queryset = Term.objects.filter(
                    Q(academic_year__school=chosen) | Q(academic_year__school__isnull=True))
                self.fields['fee_category'].queryset = FeeCategory.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True), is_active=True)
                self.fields['classes'].queryset = SchoolClass.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True), is_active=True)
                self.fields['student'].queryset = Student.objects.filter(
                    Q(school=chosen) | Q(school__isnull=True), is_active=True)
        years = self.fields['academic_year'].queryset
        if not self.fields['academic_year'].initial:
            ay = years.filter(is_active=True).first()
            if ay:
                self.fields['academic_year'].initial = ay.pk
                first_term = (Term.objects
                              .filter(academic_year=ay)
                              .order_by('term_number').first())
                if first_term:
                    self.fields['term'].initial = first_term.pk
        self.fields['student'].label_from_instance = (
            lambda s: f"{s.student_id} - {s.get_full_name()} ({s.school_class.name})"
        )

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        return amount

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get('scope')
        ay = cleaned.get('academic_year')
        term = cleaned.get('term')
        school = cleaned.get('school')
        if ay and term and ay.pk != term.academic_year_id:
            self.add_error('term', 'Selected term does not belong to the selected academic year.')
        if school is not None:
            selections = {
                'academic_year': getattr(ay, 'school_id', None),
                'term': getattr(getattr(term, 'academic_year', None), 'school_id', None),
                'fee_category': getattr(cleaned.get('fee_category'), 'school_id', None),
            }
            for name, sid in selections.items():
                # Legacy (school-less) selections are allowed; they are adopted
                # into the chosen school when the fee is assigned.
                if cleaned.get(name) is not None and sid not in (None, school.pk):
                    self.add_error(
                        name,
                        'This selection belongs to a different school than the one chosen.')
        if scope == self.SCOPE_CLASSES and not cleaned.get('classes'):
            self.add_error('classes', 'Select at least one class, or choose a different scope.')
        if scope == self.SCOPE_SINGLE and not cleaned.get('student'):
            self.add_error('student', 'Select a student to assign the fee to.')
        return cleaned

    def adopt_school(self, school):
        """Attach legacy (school-less) selections to ``school`` on first use.

        Called by the view once the target branch is known, so records created
        before multi-school are claimed by the branch the administrator chose
        instead of blocking the fee assignment.
        """
        if school is None:
            return
        _adopt_school(
            school,
            self.cleaned_data.get('academic_year'),
            self.cleaned_data.get('term'),
            self.cleaned_data.get('fee_category'),
        )
        for school_class in self.cleaned_data.get('classes') or []:
            _adopt_school(school, school_class)
        student = self.cleaned_data.get('student')
        if student is not None:
            _adopt_school(school, student.school_class)
            if student.school_id is None:
                student.school = school
                student.save(update_fields=['school'])


class ReminderTemplateForm(forms.ModelForm):
    class Meta:
        model = ReminderTemplate
        fields = ['name', 'reminder_type', 'message', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'reminder_type': forms.Select(attrs={'class': 'form-select'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
