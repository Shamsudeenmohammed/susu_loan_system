"""Settings for rehearsing migrations against a throw-away database copy.

Point the ``REHEARSAL_DB`` environment variable at a *copy* of the
authoritative database and run management commands against it. The real
``db.sqlite3`` is never touched.

Windows PowerShell:

    Copy-Item db.sqlite3 backups\\rehearsal.sqlite3
    $env:REHEARSAL_DB = "backups\\rehearsal.sqlite3"
    python manage.py migrate --settings=config.settings.rehearsal
    python manage.py school_status --settings=config.settings.rehearsal
"""

import os

from .development import *  # noqa: F401,F403

_db = os.environ.get('REHEARSAL_DB')
if not _db:
    raise RuntimeError(
        'REHEARSAL_DB is not set. Point it at a copy of the database you want '
        'to rehearse against; the authoritative db.sqlite3 is never touched.')

DATABASES['default']['NAME'] = _db  # noqa: F405

# A rehearsal must never deliver real email or SMS.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
CELERY_TASK_ALWAYS_EAGER = True
