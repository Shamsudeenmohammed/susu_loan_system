from django.conf import settings


def site_context(request):
    return {
        'organization_name': getattr(settings, 'ORGANIZATION_NAME', 'Zemzem Savings and Loans'),
        'site_url': getattr(settings, 'SITE_URL', 'http://localhost:8000'),
    }
