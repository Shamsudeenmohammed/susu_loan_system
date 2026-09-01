from django import template
from decimal import Decimal

register = template.Library()


@register.filter
def has_role(user, roles):
    """Check if user has any of the comma-separated roles.

    Usage: {% if user|has_role:"SUPER_ADMIN,ADMIN,MANAGER" %}
    """
    if not user or not hasattr(user, 'has_role'):
        return False
    role_list = [r.strip() for r in roles.split(',')]
    return user.has_role(*role_list)


@register.filter
def currency(value):
    """Format value as GHS currency."""
    if value is None:
        value = Decimal('0.00')
    return f"GHS {value:,.2f}"


@register.filter
def status_badge_class(status):
    """Return Bootstrap badge class for a status."""
    status_map = {
        'ACTIVE': 'bg-success',
        'APPROVED': 'bg-success',
        'PAID': 'bg-success',
        'COMPLETED': 'bg-success',
        'DISBURSED': 'bg-info',
        'PENDING': 'bg-warning',
        'UNDER_REVIEW': 'bg-warning',
        'PARTIALLY_PAID': 'bg-warning',
        'REQUESTED': 'bg-warning',
        'SUBMITTED': 'bg-warning',
        'REJECTED': 'bg-danger',
        'OVERDUE': 'bg-danger',
        'DEFAULTED': 'bg-danger',
        'FAILED': 'bg-danger',
        'CANCELLED': 'bg-secondary',
        'INACTIVE': 'bg-secondary',
        'SENT': 'bg-info',
        'DELIVERED': 'bg-success',
        'SCHEDULED': 'bg-info',
        'SENDING': 'bg-info',
        'PARTIAL': 'bg-warning',
        'DRAFT': 'bg-secondary',
        'QUEUED': 'bg-warning',
    }
    return status_map.get(str(status).upper(), 'bg-secondary')


@register.filter
def sms_units(value):
    """Return an SMS units count as a small human string."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    return f"{n} unit" if n == 1 else f"{n} units"



@register.filter
def has_group(user, group_name):
    """Check if user belongs to a specific group."""
    return user.groups.filter(name=group_name).exists()
