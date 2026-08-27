import pytest
from apps.core.utils import (
    generate_unique_number, format_currency, normalize_ghana_phone,
    validate_ghana_phone
)
from decimal import Decimal


@pytest.mark.django_db
class TestCoreUtils:
    def test_generate_unique_number(self):
        n1 = generate_unique_number('TXN')
        n2 = generate_unique_number('TXN')
        assert n1 != n2
        assert n1.startswith('TXN-')
        assert n2.startswith('TXN-')

    def test_generate_prefixed_numbers(self):
        c = generate_unique_number('CUS')
        s = generate_unique_number('SUS')
        l = generate_unique_number('LN')
        assert c.startswith('CUS-')
        assert s.startswith('SUS-')
        assert l.startswith('LN-')

    def test_format_currency(self):
        assert format_currency(Decimal('1000.50')) == 'GHS 1,000.50'
        assert format_currency(Decimal('0')) == 'GHS 0.00'
        assert format_currency(None) == 'GHS 0.00'

    def test_normalize_ghana_phone(self):
        assert normalize_ghana_phone('0241234567') == '+233241234567'
        assert normalize_ghana_phone('0541234567') == '+233541234567'
        assert normalize_ghana_phone('233241234567') == '+233241234567'
        assert normalize_ghana_phone('+233241234567') == '+233241234567'

    def test_validate_ghana_phone(self):
        assert validate_ghana_phone('+233241234567')
