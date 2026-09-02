import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
class TestUserModel:
    def test_create_user(self):
        user = User.objects.create_user(
            username='testuser',
            email='test@test.com', password='pass123',
            first_name='Test', role='CASHIER'
        )
        assert user.email == 'test@test.com'
        assert user.username == 'testuser'
        assert user.role == 'CASHIER'
        assert user.check_password('pass123')
        assert user.is_active

    def test_create_superuser(self):
        user = User.objects.create_superuser(
            username='adminuser',
            email='admin@test.com', password='pass123'
        )
        assert user.is_staff
        assert user.is_superuser
        assert user.role == 'SUPER_ADMIN'

    def test_has_role(self):
        user = User.objects.create_user(
            username='mgruser',
            email='mgr@test.com', password='pass123', role='MANAGER'
        )
        assert user.has_role('MANAGER', 'ADMIN')
        assert not user.has_role('CASHIER', 'CUSTOMER')

    def test_is_staff_member(self):
        staff = User.objects.create_user(username='staffuser', email='s@test.com', password='p', role='CASHIER')
        cust = User.objects.create_user(username='custuser', email='c@test.com', password='p', role='CUSTOMER')
        assert staff.is_staff_member
        assert not cust.is_staff_member

    def test_user_str(self):
        user = User.objects.create_user(
            username='johndoe',
            email='test@test.com', password='pass123',
            first_name='John', last_name='Doe'
        )
        assert str(user) == 'John Doe (test@test.com)'
