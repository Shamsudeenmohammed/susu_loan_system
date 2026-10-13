"""Idempotently provision the two school branches and their staff accounts.

Usage examples (Windows PowerShell):

    # Create/refresh just the two schools
    python manage.py setup_schools

    # Create schools *and* the proprietor + head teacher accounts
    python manage.py setup_schools --with-users ^
        --proprietor-username proprietor --proprietor-password "S3cret..." ^
        --mankarigu-username mankarigu_head --mankarigu-password "S3cret..." ^
        --lingbinsi-username lingbinsi_head --lingbinsi-password "S3cret..."

Passwords may also be supplied through environment variables
(``PROPRIETOR_PASSWORD``, ``MANKARIGU_HEAD_PASSWORD``,
``LINGBINSI_HEAD_PASSWORD``) so they never appear in shell history.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.schools.models import School

User = get_user_model()

SCHOOLS = [
    {
        'code': 'MANKARIGU',
        'name': 'Zemzem Golden Child Academy \u2013 Mankarigu',
        'short_name': 'Zemzem Mankarigu',
        'location': 'Mankarigu',
    },
    {
        'code': 'LINGBINSI',
        'name': 'Zemzem Golden Child Academy \u2013 Lingbinsi',
        'short_name': 'Zemzem Lingbinsi',
        'location': 'Lingbinsi',
    },
]


class Command(BaseCommand):
    help = 'Create the two Zemzem school branches (and optionally staff accounts).'

    def add_arguments(self, parser):
        parser.add_argument('--with-users', action='store_true',
                            help='Also create/refresh proprietor and head teacher accounts.')
        parser.add_argument('--proprietor-username', default='proprietor')
        parser.add_argument('--proprietor-password', default=None)
        parser.add_argument('--mankarigu-username', default='mankarigu_head')
        parser.add_argument('--mankarigu-password', default=None)
        parser.add_argument('--lingbinsi-username', default='lingbinsi_head')
        parser.add_argument('--lingbinsi-password', default=None)

    @transaction.atomic
    def handle(self, *args, **options):
        created_schools = {}
        for spec in SCHOOLS:
            school, created = School.objects.update_or_create(
                code=spec['code'],
                defaults={
                    'name': spec['name'],
                    'short_name': spec['short_name'],
                    'location': spec['location'],
                    'is_active': True,
                },
            )
            created_schools[spec['code']] = school
            state = 'Created' if created else 'Found'
            self.stdout.write(f"{state} school: {school.name}")

        if not options['with_users']:
            self.stdout.write(self.style.WARNING(
                'Schools only. Re-run with --with-users to create staff accounts.'))
            return

        self._upsert_user(
            username=options['proprietor_username'],
            password=options['proprietor_password'] or os.environ.get('PROPRIETOR_PASSWORD'),
            role='PROPRIETOR',
            first_name='Proprietor',
            last_name='',
            school=None,
            label='proprietor',
        )
        self._upsert_user(
            username=options['mankarigu_username'],
            password=options['mankarigu_password'] or os.environ.get('MANKARIGU_HEAD_PASSWORD'),
            role='HEAD_TEACHER',
            first_name='Head Teacher',
            last_name='Mankarigu',
            school=created_schools['MANKARIGU'],
            label='Mankarigu head teacher',
        )
        self._upsert_user(
            username=options['lingbinsi_username'],
            password=options['lingbinsi_password'] or os.environ.get('LINGBINSI_HEAD_PASSWORD'),
            role='HEAD_TEACHER',
            first_name='Head Teacher',
            last_name='Lingbinsi',
            school=created_schools['LINGBINSI'],
            label='Lingbinsi head teacher',
        )
        self.stdout.write(self.style.SUCCESS('School accounts are ready.'))

    def _upsert_user(self, username, password, role, first_name, last_name, school, label):
        if not username:
            raise CommandError(f'No username supplied for the {label}.')
        user, created = User.objects.get_or_create(
            username=username.lower(),
            defaults={
                'role': role,
                'first_name': first_name,
                'last_name': last_name,
                'school': school,
                'is_active': True,
            },
        )
        changed = False
        if not created:
            if user.role != role:
                user.role = role
                changed = True
            if user.school_id != getattr(school, 'pk', None):
                user.school = school
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
        if password:
            user.set_password(password)
            changed = True
        elif created:
            self.stdout.write(self.style.WARNING(
                f"  {label} '{user.username}' was created without a password and "
                f"cannot log in until one is set (re-run with the matching "
                f"--*-password option or environment variable)."))
        if created or changed:
            user.save()
        state = 'Created' if created else ('Updated' if changed else 'Found')
        self.stdout.write(f"{state} {label}: {user.username}")
