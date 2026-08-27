import pytest
from apps.audit.models import AuditLog


@pytest.mark.django_db
class TestAuditLog:
    def test_log_action(self, admin_user):
        log = AuditLog.log(
            action='CONTRIBUTION_CREATED',
            description='Test contribution',
            user=admin_user,
            object_type='Transaction',
            object_id=1,
        )
        assert log.pk is not None
        assert log.action == 'CONTRIBUTION_CREATED'

    def test_log_str(self, admin_user):
        log = AuditLog.log(
            action='LOGIN',
            description='User logged in',
            user=admin_user,
        )
        assert 'admin' in str(log).lower()

    def test_audit_entries_created(self, admin_user):
        AuditLog.log('CUSTOMER_CREATED', 'Created customer', user=admin_user)
        AuditLog.log('CONTRIBUTION_CREATED', 'Recorded contribution', user=admin_user)
        assert AuditLog.objects.count() == 2
