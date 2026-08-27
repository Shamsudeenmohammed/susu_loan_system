import string
import random
from decimal import Decimal
from datetime import datetime


def generate_unique_number(prefix='TXN', length=6):
    """Generate a unique sequential number like TXN-000001."""
    from apps.core.models import SequenceCounter
    counter = SequenceCounter.objects.get_or_create(prefix=prefix)[0]
    counter.counter += 1
    counter.save()
    return f"{prefix}-{counter.counter:0{length}d}"


def format_currency(amount):
    """Format amount as GHS currency string."""
    if amount is None:
        amount = Decimal('0.00')
    return f"GHS {amount:,.2f}"


def normalize_ghana_phone(phone):
    """
    Normalize Ghanaian phone numbers to +233XXXXXXXXX format.
    Supports: 024XXXXXXX, 054XXXXXXX, 055XXXXXXX, 020XXXXXXX, 050XXXXXXX
    """
    import phonenumbers
    if not phone:
        return phone

    phone = phone.strip().replace(' ', '').replace('-', '')

    if phone.startswith('+233'):
        return phone
    elif phone.startswith('233'):
        return f'+{phone}'
    elif phone.startswith('0'):
        return f'+233{phone[1:]}'

    return phone


def validate_ghana_phone(phone):
    """Validate a phone number, defaulting to Ghana."""
    import phonenumbers
    try:
        parsed = phonenumbers.parse(phone, 'GH')
        return phonenumbers.is_valid_number(parsed)
    except phonenumbers.NumberParseException:
        return False


def get_financial_year():
    """Get current financial year based on Ghana calendar."""
    now = datetime.now()
    return now.year


def calculate_days_between(start_date, end_date):
    """Calculate days between two dates."""
    delta = end_date - start_date
    return delta.days
