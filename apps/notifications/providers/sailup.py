import logging

import requests
from django.conf import settings

from .base import SMSProvider
from .exceptions import (
    SMSProviderAuthError,
    SMSProviderError,
    SMSProviderUnavailableError,
)

logger = logging.getLogger('apps.notifications')


class SailupSMSService(SMSProvider):
    """
    Sailup SMS provider implementation.

    Official docs: https://www.sailup.io/docs

    Sailup accepts both local (0201234567) and E.164 (+233201234567) formats,
    so we always send the normalized E.164 number.
    """

    name = 'sailup'

    def __init__(self, api_key=None, base_url=None, sender_id=None, timeout=None):
        self.api_key = api_key if api_key is not None else getattr(settings, 'SAILUP_API_KEY', '')
        self.base_url = (base_url if base_url is not None
                         else getattr(settings, 'SAILUP_BASE_URL', 'https://api.sailup.io/v1')).rstrip('/')
        self.sender_id = sender_id if sender_id is not None else getattr(settings, 'SAILUP_SENDER_ID', '')
        self.timeout = timeout if timeout is not None else getattr(settings, 'SAILUP_TIMEOUT', 10)

    @property
    def enabled(self):
        return bool(self.api_key)

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    @staticmethod
    def _parse_error(response):
        try:
            data = response.json()
            return data.get('detail') or data.get('error') or data.get('message') or response.text
        except Exception:
            return response.text[:500]

    def send_sms(self, phone_number, message, sender_id=None):
        if not self.api_key:
            raise SMSProviderAuthError('SAILUP_API_KEY is not configured')

        sender = sender_id or self.sender_id
        url = f'{self.base_url}/sms/'
        payload = {
            'from': sender,
            'to': [phone_number],
            'body': message,
        }

        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise SMSProviderUnavailableError('Sailup request timed out') from exc
        except requests.exceptions.ConnectionError as exc:
            raise SMSProviderUnavailableError('Could not connect to Sailup') from exc
        except requests.exceptions.RequestException as exc:
            raise SMSProviderUnavailableError(f'Sailup request error: {exc}') from exc

        if response.status_code in (200, 201, 202):
            try:
                data = response.json()
            except ValueError:
                raise SMSProviderError('Sailup returned an invalid response')
            message_id = str(data.get('id', ''))
            status = data.get('status', '') or 'queued'
            return {
                'provider_message_id': message_id,
                'status': status,
            }

        if response.status_code == 401:
            raise SMSProviderAuthError('Sailup authentication failed (401)')
        if response.status_code == 403:
            raise SMSProviderAuthError('Sailup authorization failed (403)')

        raise SMSProviderError(
            f'Sailup API error {response.status_code}: {self._parse_error(response)}'
        )

    def get_message_status(self, provider_message_id):
        if not self.api_key:
            raise SMSProviderAuthError('SAILUP_API_KEY is not configured')
        if not provider_message_id:
            raise SMSProviderError('No provider message id to query')

        url = f'{self.base_url}/sms/{provider_message_id}/'
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise SMSProviderUnavailableError(f'Sailup status lookup failed: {exc}') from exc

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                raise SMSProviderError('Sailup returned an invalid status response')
            return data.get('delivery_status') or data.get('status') or ''

        if response.status_code == 401:
            raise SMSProviderAuthError('Sailup authentication failed (401)')

        raise SMSProviderError(
            f'Sailup status error {response.status_code}: {self._parse_error(response)}'
        )
