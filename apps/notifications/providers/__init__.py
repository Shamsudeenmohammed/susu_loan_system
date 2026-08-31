from django.conf import settings

from .base import SMSProvider
from .sailup import SailupSMSService


def get_provider(provider_name=None):
    """
    Factory that returns the active SMS provider instance.

    The provider name defaults to the configured SMS_PROVIDER setting, which
    defaults to 'sailup'. New providers can be registered here and selected
    via configuration without touching the rest of the application.
    """
    name = provider_name or getattr(settings, 'SMS_PROVIDER', 'sailup')

    if name == 'sailup':
        return SailupSMSService()

    raise ValueError(f'Unknown SMS provider: {name}')
