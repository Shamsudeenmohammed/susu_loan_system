import os
import dj_database_url
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-change-this-in-production')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'rest_framework',
    'crispy_forms',
    'crispy_bootstrap5',
    'django_filters',
    'corsheaders',
    'apps.core',
    'apps.accounts',
    'apps.customers',
    'apps.susu',
    'apps.loans',
    'apps.payments',
    'apps.notifications',
    'apps.campaigns',
    'apps.reports',
    'apps.audit',
    'apps.dashboard',
    'apps.school_fees.apps.SchoolFeesConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.site_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = '/accounts/login/'
LOGOUT_REDIRECT_URL = 'login'

AUTH_PASSWORD_VALIDATORS = [
    # {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    # {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    # {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    # {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TIME_ZONE', 'Africa/Accra')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
}

# Security
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Sailup SMS
SMS_PROVIDER = os.environ.get('SMS_PROVIDER', 'sailup')
SAILUP_API_KEY = os.environ.get('SAILUP_API_KEY', '')
SAILUP_BASE_URL = os.environ.get('SAILUP_BASE_URL', 'https://api.sailup.io/v1')
SAILUP_SENDER_ID = os.environ.get('SAILUP_SENDER_ID', 'ZEMZEM')
SAILUP_TIMEOUT = int(os.environ.get('SAILUP_TIMEOUT', '10'))
SAILUP_ENABLED = os.environ.get('SAILUP_ENABLED', 'False') == 'True'
SMS_TEST_MODE = os.environ.get('SMS_TEST_MODE', 'True') == 'True'

# Paystack
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_WEBHOOK_SECRET = os.environ.get('PAYSTACK_WEBHOOK_SECRET', '')

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@zemzemsavings.com')

# Site
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:8000')
ORGANIZATION_NAME = os.environ.get('ORGANIZATION_NAME', 'Zemzem Savings and Loans')
# Branding for the School Fees Management module (separate from the core
# savings/loans organisation branding).
SCHOOL_NAME = os.environ.get('SCHOOL_NAME', 'Zemzem Golden Child Academy')

# Customer support contact lines.
TECH_SUPPORT_PHONES = os.environ.get('TECH_SUPPORT_PHONES', '0247213850,0556459984')
COMPLAINT_SUPPORT_PHONES = os.environ.get('COMPLAINT_SUPPORT_PHONES', '0597902220,0200638682')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# ==============================================================================
# DJANGO JAZZMIN - Admin Backend Theme
# ==============================================================================
JAZZMIN_SETTINGS = {
    # ------------------------------------------------------------------
    # Branding
    # ------------------------------------------------------------------
    'site_title': 'Zemzem Savings and Loans',
    'site_header': 'Zemzem Savings and Loans',
    'site_brand': 'Zemzem',
    'welcome_sign': 'Welcome to the Zemzem Savings and Loans Admin Portal',
    'copyright': 'Zemzem Savings and Loans',
    'site_logo': 'jazzmin-custom/logo.svg',
    'login_logo': 'jazzmin-custom/logo.svg',
    'site_logo_classes': 'img-circle',
    'site_icon': 'jazzmin-custom/logo.svg',
    'show_sidebar': True,
    'navigation_expanded': True,

    # ------------------------------------------------------------------
    # Search — single box in the navbar keeps the top bar aligned
    # ------------------------------------------------------------------
    'search_model': 'customers.Customer',
    'search_sources': ['customers.Customer', 'susu.SusuAccount', 'payments.Transaction', 'loans.Loan'],

    # ------------------------------------------------------------------
    # Icons (Font Awesome) for models and apps
    # ------------------------------------------------------------------
    'icons': {
        'auth.User': 'fas fa-user',
        'accounts.User': 'fas fa-user-shield',
        'customers.Customer': 'fas fa-users',
        'susu.SusuAccount': 'fas fa-piggy-bank',
        'loans.LoanProduct': 'fas fa-tags',
        'loans.LoanPolicy': 'fas fa-book',
        'loans.Loan': 'fas fa-file-invoice-dollar',
        'loans.RepaymentSchedule': 'fas fa-calendar-alt',
        'loans.LoanRepayment': 'fas fa-money-bill-wave',
        'loans.EligibilityAudit': 'fas fa-clipboard-check',
        'payments.Transaction': 'fas fa-exchange-alt',
        'payments.Withdrawal': 'fas fa-hand-holding-usd',
        'notifications.SMSNotification': 'fas fa-sms',
        'audit.AuditLog': 'fas fa-history',
    },
    'default_icon_parents': 'fas fa-chevron-circle-down',
    'default_icon_children': 'fas fa-circle',

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------
    'order_with_respect_to': [
        'customers',
        'susu',
        'payments',
        'loans',
        'notifications',
        'audit',
        'accounts',
        'auth',
    ],
    'related_modal_active': False,
    'show_ui_builder': False,

    # ------------------------------------------------------------------
    # Top menu
    # ------------------------------------------------------------------
    'topmenu_links': [
        {'name': 'Home', 'url': 'admin:index', 'new_window': False},
        {'model': 'customers.Customer'},
        {'model': 'payments.Transaction'},
        {'model': 'loans.Loan'},
        {'app': 'audit'},
        {'app': 'reports'},
        {'name': 'Support', 'url': 'https://zemzemsavings.com', 'new_window': True},
    ],

    # ------------------------------------------------------------------
    # Forms / navigation
    # ------------------------------------------------------------------
    'show_changelist_header': True,
    'changeform_format': 'horizontal_tabs',
    'add_changeform_link_back': False,
    'custom_js': None,
    'usermenu_links': [
        {'name': 'View Customer Site', 'url': '/', 'new_window': True},
    ],
    'langmenu_active': False,
}
