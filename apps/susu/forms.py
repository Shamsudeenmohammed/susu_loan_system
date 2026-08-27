from django import forms
from django.conf import settings
from decimal import Decimal
from .models import SusuAccount
from apps.customers.models import Customer


class SusuAccountForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.filter(status='ACTIVE'),
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='Select Customer'
    )
    contribution_frequency = forms.ChoiceField(
        choices=SusuAccount.Frequency.choices,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    expected_contribution = forms.DecimalField(
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'})
    )
    target_amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'})
    )

    class Meta:
        model = SusuAccount
        fields = ['customer', 'contribution_frequency', 'expected_contribution', 'target_amount']
