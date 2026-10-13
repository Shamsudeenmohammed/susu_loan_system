"""End-to-end check: the school selector really drives the fee dashboard.

Uses Django's test client against the live database (read-mostly; it only
touches the session table, which is cleaned up afterwards).
"""
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.schools.models import School
from apps.schools.scoping import SESSION_KEY

U = get_user_model()
user = U.objects.filter(role='SUPER_ADMIN', is_active=True).first()
client = Client(SERVER_NAME='localhost')
client.force_login(user)

man = School.objects.get(code='MANKARIGU')
lin = School.objects.get(code='LINGBINSI')


def fee_totals():
    resp = client.get(reverse('school_fees_dashboard'))
    ctx = resp.context
    return (resp.status_code, ctx['total_students'], ctx['total_fees'],
            ctx['total_collected'], ctx['total_outstanding'])


print('COMBINED              :', fee_totals())

client.post(reverse('switch_school'), {'school': man.pk, 'next': reverse('school_fees_dashboard')})
print('active_school=Mankarigu:', client.session.get(SESSION_KEY))
print('MANKARIGU             :', fee_totals())

client.post(reverse('switch_school'), {'school': lin.pk, 'next': reverse('school_fees_dashboard')})
print('active_school=Lingbinsi:', client.session.get(SESSION_KEY))
print('LINGBINSI             :', fee_totals())
