from django import forms
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal

from apps.core.utils import normalize_ghana_phone

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


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ['name', 'code', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ['name', 'start_date', 'end_date', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['academic_year'].queryset = AcademicYear.objects.all()
        if not self.fields['academic_year'].initial:
            ay = AcademicYear.objects.filter(is_active=True).first()
            if ay:
                self.fields['academic_year'].initial = ay.pk


class FeeCategoryForm(forms.ModelForm):
    class Meta:
        model = FeeCategory
        fields = ['name', 'code', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
        }


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
        fields = ['academic_year', 'term', 'school_class', 'fee_category',
                  'amount', 'due_date', 'description', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_active'].widget.attrs.update({'class': 'form-check-input'})

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        return amount

    def clean(self):
        cleaned = super().clean()
        ay = cleaned.get('academic_year')
        term = cleaned.get('term')
        if ay and term and ay.pk != term.academic_year_id:
            self.add_error('term', 'Selected term does not belong to the selected academic year.')
        return cleaned


class StudentForm(forms.ModelForm):
    school_class = forms.ModelChoiceField(
        queryset=SchoolClass.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    parent_phone = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 0241234567'}))

    class Meta:
        model = Student
        fields = ['first_name', 'last_name', 'school_class', 'parent_name',
                  'parent_phone', 'parent_email', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_name': forms.TextInput(attrs={'class': 'form-control'}),
            'parent_email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

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


class ReminderTemplateForm(forms.ModelForm):
    class Meta:
        model = ReminderTemplate
        fields = ['name', 'reminder_type', 'message', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'reminder_type': forms.Select(attrs={'class': 'form-select'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
