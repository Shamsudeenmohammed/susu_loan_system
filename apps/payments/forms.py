from django import forms
from decimal import Decimal
from .models import Transaction, Withdrawal


class ContributionForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Customer'
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'})
    )
    payment_method = forms.ChoiceField(
        choices=Transaction.PaymentMethod.choices,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    reference = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Reference (optional)'})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Notes'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.customers.models import Customer
        self.fields['customer'].queryset = Customer.objects.filter(status='ACTIVE')


class WithdrawalRequestForm(forms.ModelForm):
    account = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Susu Account'
    )
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    class Meta:
        model = Withdrawal
        fields = ['account', 'amount', 'reason']

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.susu.models import SusuAccount
        if customer:
            self.fields['account'].queryset = SusuAccount.objects.filter(
                customer=customer, status='ACTIVE'
            )
        else:
            self.fields['account'].queryset = SusuAccount.objects.filter(status='ACTIVE')

    def clean(self):
        cleaned_data = super().clean()
        account = cleaned_data.get('account')
        amount = cleaned_data.get('amount')
        if account and amount:
            if amount > account.current_balance:
                self.add_error(
                    'amount',
                    f'Insufficient balance. Available: GHS {account.current_balance:.2f}'
                )
        return cleaned_data


class WithdrawalReviewForm(forms.Form):
    status = forms.ChoiceField(
        choices=[('APPROVED', 'Approve'), ('REJECTED', 'Reject')],
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    review_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3})
    )
