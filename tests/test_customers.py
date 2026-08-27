import pytest
from decimal import Decimal
from apps.customers.models import Customer


@pytest.mark.django_db
class TestCustomer:
    def test_create_customer(self, admin_user):
        c = Customer.objects.create(
            first_name='Kwame', last_name='Asante',
            phone='0241234567', registered_by=admin_user
        )
        assert c.customer_number.startswith('CUS-')
        assert c.status == 'ACTIVE'

    def test_customer_number_unique(self, admin_user):
        c1 = Customer.objects.create(first_name='A', last_name='B', phone='0241111111', registered_by=admin_user)
        c2 = Customer.objects.create(first_name='C', last_name='D', phone='0242222222', registered_by=admin_user)
        assert c1.customer_number != c2.customer_number

    def test_get_full_name(self, admin_user):
        c = Customer.objects.create(first_name='Kwame', last_name='Asante', phone='0241234567', registered_by=admin_user)
        assert c.get_full_name() == 'Kwame Asante'

    def test_customer_str(self, admin_user):
        c = Customer.objects.create(first_name='Kwame', last_name='Asante', phone='0241234567', registered_by=admin_user)
        assert 'CUS-' in str(c)
        assert 'Kwame Asante' in str(c)
