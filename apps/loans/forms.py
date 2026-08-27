from django import forms
from decimal import Decimal
from .models import Loan, LoanProduct, RepaymentSchedule, LoanPolicy


class LoanProductForm(forms.ModelForm):
    class Meta:
        model = LoanProduct
        fields = [
            'name', 'code', 'description', 'min_amount', 'max_amount',
            'interest_rate', 'interest_method', 'min_term', 'max_term',
            'repayment_frequency', 'processing_fee_percentage',
            'late_payment_penalty', 'is_active'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'min_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'max_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'interest_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'interest_method': forms.Select(attrs={'class': 'form-select'}),
            'min_term': forms.NumberInput(attrs={'class': 'form-control'}),
            'max_term': forms.NumberInput(attrs={'class': 'form-control'}),
            'repayment_frequency': forms.Select(attrs={'class': 'form-select'}),
            'processing_fee_percentage': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'late_payment_penalty': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class LoanApplicationForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Customer'
    )
    loan_product = forms.ModelChoiceField(
        queryset=LoanProduct.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Loan Product'
    )
    principal_amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    term_months = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    purpose = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Loan purpose'})
    )
    income_info = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Income information'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.customers.models import Customer
        self.fields['customer'].queryset = Customer.objects.filter(status='ACTIVE')

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('loan_product')
        amount = cleaned_data.get('principal_amount')
        term = cleaned_data.get('term_months')

        if product and amount:
            if amount < product.min_amount:
                raise forms.ValidationError(f'Minimum amount is GHS {product.min_amount:.2f}')
            if amount > product.max_amount:
                raise forms.ValidationError(f'Maximum amount is GHS {product.max_amount:.2f}')

        if product and term:
            if term < product.min_term:
                raise forms.ValidationError(f'Minimum term is {product.min_term} months')
            if term > product.max_term:
                raise forms.ValidationError(f'Maximum term is {product.max_term} months')

        return cleaned_data


class LoanReviewForm(forms.Form):
    DECISION_CHOICES = [
        ('APPROVE', 'Approve'),
        ('REJECT', 'Reject'),
    ]
    decision = forms.ChoiceField(
        choices=DECISION_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3})
    )


class RepaymentForm(forms.Form):
    loan = forms.ModelChoiceField(
        queryset=Loan.objects.filter(status__in=['ACTIVE', 'DISBURSED', 'APPROVED']),
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Loan'
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    payment_method = forms.ChoiceField(
        choices=[('CASH', 'Cash'), ('MOBILE_MONEY', 'Mobile Money'), ('BANK', 'Bank'), ('OTHER', 'Other')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    reference = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Reference'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if customer:
            self.fields['loan'].queryset = Loan.objects.filter(
                customer=customer,
                status__in=['ACTIVE', 'DISBURSED', 'APPROVED']
            )


class LoanPolicyForm(forms.ModelForm):
    class Meta:
        model = LoanPolicy
        fields = [
            'name', 'minimum_membership_days', 'minimum_contribution_days',
            'minimum_successful_contributions', 'minimum_savings',
            'maximum_loan_multiplier', 'maximum_active_loans',
            'maximum_missed_periods', 'waiting_period_days',
            'require_kyc', 'require_good_repayment_history',
            'block_overdue_customers', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'minimum_membership_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'minimum_contribution_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'minimum_successful_contributions': forms.NumberInput(attrs={'class': 'form-control'}),
            'minimum_savings': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'maximum_loan_multiplier': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'maximum_active_loans': forms.NumberInput(attrs={'class': 'form-control'}),
            'maximum_missed_periods': forms.NumberInput(attrs={'class': 'form-control'}),
            'waiting_period_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'require_kyc': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'require_good_repayment_history': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'block_overdue_customers': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
