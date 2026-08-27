import logging
import hashlib
import hmac
import requests
from decimal import Decimal
from django.conf import settings

logger = logging.getLogger('apps.payments')


def initialize_payment(amount, email, reference, callback_url, metadata=None):
    """
    Initialize a Paystack transaction.
    Returns dict with authorization_url, access_code, reference or error.
    """
    url = 'https://api.paystack.co/transaction/initialize'
    headers = {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type': 'application/json',
    }
    payload = {
        'amount': int(amount * 100),  # Paystack uses kobo/pesewas
        'email': email,
        'reference': reference,
        'callback_url': callback_url,
        'currency': 'GHS',
    }
    if metadata:
        payload['metadata'] = metadata

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()
        if data.get('status'):
            return {
                'status': True,
                'authorization_url': data['data']['authorization_url'],
                'access_code': data['data']['access_code'],
                'reference': data['data']['reference'],
            }
        else:
            logger.error(f"Paystack init failed: {data.get('message', 'Unknown error')}")
            return {'status': False, 'message': data.get('message', 'Payment initialization failed')}
    except requests.exceptions.Timeout:
        logger.error("Paystack init timeout")
        return {'status': False, 'message': 'Payment service timed out. Please try again.'}
    except requests.exceptions.ConnectionError:
        logger.error("Paystack connection error")
        return {'status': False, 'message': 'Could not connect to payment service.'}
    except Exception as e:
        logger.exception(f"Paystack init error: {e}")
        return {'status': False, 'message': 'An unexpected error occurred.'}


def verify_payment(reference):
    """
    Verify a Paystack transaction by reference.
    Returns dict with status, amount, reference, metadata or error.
    """
    url = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        data = response.json()
        if data.get('status'):
            tx_data = data['data']
            return {
                'status': True,
                'amount': Decimal(str(tx_data['amount'])) / 100,
                'reference': tx_data['reference'],
                'gateway_response': tx_data.get('gateway_response', ''),
                'metadata': tx_data.get('metadata', {}),
                'channel': tx_data.get('channel', ''),
                'paid_at': tx_data.get('paid_at'),
            }
        else:
            logger.error(f"Paystack verify failed: {data.get('message', 'Unknown error')}")
            return {'status': False, 'message': data.get('message', 'Verification failed')}
    except requests.exceptions.Timeout:
        logger.error("Paystack verify timeout")
        return {'status': False, 'message': 'Verification service timed out.'}
    except requests.exceptions.ConnectionError:
        logger.error("Paystack verify connection error")
        return {'status': False, 'message': 'Could not connect to verification service.'}
    except Exception as e:
        logger.exception(f"Paystack verify error: {e}")
        return {'status': False, 'message': 'An unexpected error occurred.'}


def verify_webhook_signature(payload_body, signature_header):
    """
    Verify Paystack webhook signature for security.
    Returns True if signature is valid.
    """
    secret = getattr(settings, 'PAYSTACK_WEBHOOK_SECRET', '')
    if not secret:
        logger.warning("PAYSTACK_WEBHOOK_SECRET not configured, skipping signature verification")
        return True

    try:
        computed = hmac.new(
            secret.encode('utf-8'),
            payload_body,
            hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(computed, signature_header)
    except Exception as e:
        logger.exception(f"Webhook signature verification error: {e}")
        return False
