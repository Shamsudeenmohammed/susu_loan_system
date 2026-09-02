from django import forms
from .models import Customer


class CustomerForm(forms.ModelForm):
    first_name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'})
    )
    last_name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'})
    )
    phone = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone Number (e.g., 024XXXXXXX)'})
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email (optional)'})
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    gender = forms.ChoiceField(
        choices=[('', 'Select Gender')] + list(Customer.Gender.choices),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Address'})
    )
    occupation = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Occupation'})
    )
    id_type = forms.ChoiceField(
        choices=[
            ('', 'Select ID Type'),
            ('NATIONAL_ID', 'National ID'),
            ('PASSPORT', 'Passport'),
            ('DRIVERS_LICENSE', "Driver's License"),
            ('VOTER_ID', 'Voter ID'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False
    )
    id_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ID Number'})
    )

    class Meta:
        model = Customer
        fields = [
            'first_name', 'last_name', 'date_of_birth', 'gender',
            'phone', 'email', 'address', 'occupation',
            'id_type', 'id_number', 'photo'
        ]


class CustomerSearchForm(forms.Form):
    query = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search by name, phone, or customer number...',
        })
    )
    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + list(Customer.Status.choices),
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False
    )
