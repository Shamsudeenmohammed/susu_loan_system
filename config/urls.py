import os
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('stophack/', admin.site.urls),
    path('', include('apps.dashboard.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('customers/', include('apps.customers.urls')),
    path('susu/', include('apps.susu.urls')),
    path('loans/', include('apps.loans.urls')),
    path('payments/', include('apps.payments.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('campaigns/', include('apps.campaigns.urls')),
    path('reports/', include('apps.reports.urls')),
    path('audit/', include('apps.audit.urls')),
    path('school-fees/', include('apps.school_fees.urls')),
    path('api/', include('apps.core.api_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    try:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
    except ImportError:
        pass

handler400 = 'apps.core.views.custom_bad_request'
handler403 = 'apps.core.views.custom_permission_denied'
handler404 = 'apps.core.views.custom_page_not_found'
handler500 = 'apps.core.views.custom_server_error'
