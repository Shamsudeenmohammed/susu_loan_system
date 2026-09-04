import os

from django.conf import settings


def _static_version():
    base = getattr(settings, 'BASE_DIR', None)
    if base is None:
        return '1'
    mtimes = []
    for rel in ('static/css/style.css', 'static/js/app.js'):
        path = os.path.join(str(base), rel)
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            continue
    if not mtimes:
        return '1'
    return str(int(max(mtimes)))


def site_context(request):
    def phones(value):
        return [p.strip() for p in str(value).split(',') if p.strip()]

    return {
        'organization_name': getattr(settings, 'ORGANIZATION_NAME', 'Zemzem Savings and Loans'),
        'school_name': getattr(settings, 'SCHOOL_NAME', 'Zemzem Golden Child Academy'),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
        'static_version': _static_version(),
        'tech_support_phones': phones(getattr(settings, 'TECH_SUPPORT_PHONES', '')),
        'complaint_support_phones': phones(getattr(settings, 'COMPLAINT_SUPPORT_PHONES', '')),
    }
