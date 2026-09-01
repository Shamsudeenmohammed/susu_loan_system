from django import forms
from django.core.validators import RegexValidator
from django.utils import timezone

from apps.campaigns.models import SMSCampaign, SMSTemplate, SMSMessageLog
from apps.core.utils import normalize_ghana_phone, validate_ghana_phone
from apps.customers.models import Customer

from .services.personalization import (
    CAMPAIGN_PLACEHOLDERS,
    ALL_PLACEHOLDERS,
)
from .services.recipients import resolve_recipients

PHONE_RE = RegexValidator(
    regex=r'^\+?[0-9]{9,15}$',
    message='Enter a valid phone number (e.g. +233241234567).',
)


class CampaignForm(forms.ModelForm):
    class Meta:
        model = SMSCampaign
        fields = ['name', 'campaign_type', 'target_group', 'message', 'trigger', 'scheduled_at']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. May Contribution Drive'}),
            'campaign_type': forms.Select(attrs={'class': 'form-select'}),
            'target_group': forms.Select(attrs={'class': 'form-select'}),
            'message': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 5,
                'placeholder': 'Type your message here. Use {{placeholders}} to personalize.',
            }),
            'trigger': forms.Select(attrs={'class': 'form-select'}),
            'scheduled_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['scheduled_at'].required = False

    def clean(self):
        cleaned = super().clean()
        trigger = cleaned.get('trigger')
        scheduled_at = cleaned.get('scheduled_at')
        if trigger == SMSCampaign.Trigger.SCHEDULE and not scheduled_at:
            self.add_error('scheduled_at', 'A send time is required when scheduling.')
        if trigger == SMSCampaign.Trigger.SEND_NOW:
            cleaned['scheduled_at'] = None
        return cleaned


class RecipientSelectionForm(forms.Form):
    """Manual recipient selection via customer checkboxes (MANUAL_SELECTION)."""
    customers = forms.ModelMultipleChoiceField(
        queryset=Customer.objects.order_by('first_name', 'last_name').all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )


class TemplateForm(forms.ModelForm):
    class Meta:
        model = SMSTemplate
        fields = ['name', 'campaign_type', 'message', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'campaign_type': forms.Select(attrs={'class': 'form-select'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TestSMSSendForm(forms.Form):
    phone_number = forms.CharField(
        max_length=20,
        label='Test recipient phone number',
        validators=[PHONE_RE],
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+233241234567'}),
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
    )

    def clean_phone_number(self):
        phone = normalize_ghana_phone(self.cleaned_data['phone_number'])
        if not validate_ghana_phone(phone):
            raise forms.ValidationError('Invalid phone number.')
        return phone


class RetryFailedForm(forms.Form):
    campaign = forms.ModelChoiceField(queryset=SMSCampaign.objects.none())

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['campaign'].queryset = SMSCampaign.objects.filter(
            failed_count__gt=0,
        )
