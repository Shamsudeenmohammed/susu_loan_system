# Zemzem Savings and Loans

A professional, production-ready Susu Collection and Loan Management Platform built with Django.

## Features

- **Customer Management** - Full customer profiles with registration, search, and filtering
- **Susu Accounts** - Account creation with configurable contribution frequencies (Daily, Weekly, Monthly)
- **Financial Ledger** - Immutable transaction ledger with balance-before/balance-after tracking
- **Contributions** - Record susu contributions with multiple payment methods (Cash, Mobile Money, Bank)
- **Withdrawals** - Multi-step withdrawal workflow (Requested → Under Review → Approved/Rejected → Completed)
- **Loan Products** - Configurable loan products with Flat and Reducing Balance interest methods
- **Loan Applications** - Full application workflow with eligibility validation
- **Loan Approval** - Staff review and approval/rejection with audit trail
- **Loan Disbursement** - Automated disbursement with ledger entries and repayment schedule generation
- **Repayment Schedules** - Auto-generated installment plans supporting daily/weekly/biweekly/monthly
- **Loan Repayments** - Payment recording with installment tracking and balance updates
- **SMS Notifications** - Sailup SMS integration (provider-independent, via Celery) for all key events
- **Dashboards** - Staff dashboard with charts and KPIs; customer dashboard with account overview
- **Reports** - Customer, contribution, loan, repayment, overdue, and daily summary reports with CSV export
- **Audit Log** - Comprehensive audit trail for all financial and administrative actions
- **REST API** - Django REST Framework API for customers, accounts, transactions, loans
- **Role-Based Access Control** - Super Admin, Admin, Manager, Loan Officer, Cashier, Collector, Customer
- **Ghana Context** - GHS currency, Africa/Accra timezone, Ghanaian phone number normalization

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 5.0, Django REST Framework |
| Database | PostgreSQL (production), SQLite (development) |
| Frontend | Django Templates, Bootstrap 5, Chart.js |
| Async Tasks | Celery + Redis |
| SMS | Sailup SMS (provider abstraction) |
| Testing | pytest-django, factory-boy |

## Project Structure

```
susu_loan_system/
├── config/               # Django configuration
│   ├── settings/         # base, development, production
│   ├── urls.py
│   ├── wsgi.py
│   └── celery.py
├── apps/
│   ├── accounts/         # Authentication & user roles
│   ├── customers/        # Customer management
│   ├── susu/             # Susu accounts
│   ├── payments/         # Financial ledger, contributions, withdrawals
│   ├── loans/            # Loan products, applications, repayments
│   ├── notifications/    # SMS notifications (providers, services, tasks)
│   ├── reports/          # Reporting & CSV export
│   ├── audit/            # Audit logging
│   ├── dashboard/        # Dashboard views
│   └── core/             # Utilities, decorators, API
├── templates/            # HTML templates
├── static/               # CSS, JS, images
├── tests/                # Test suite
└── requirements/         # Dependencies
```

## Installation

### Prerequisites

- Python 3.10+
- PostgreSQL (for production)
- Redis (for Celery)

### Local Development

```bash
# Clone the repository
cd susu_loan_system

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
copy .env.example .env    # Windows
cp .env.example .env      # macOS/Linux

# Edit .env with your settings (at minimum, set SECRET_KEY)

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver
```

### Running Without Redis/Celery

The system works without Redis/Celery for basic development. In development mode:
- `CELERY_TASK_ALWAYS_EAGER = True` makes Celery tasks run synchronously
- SMS is in test mode by default (`SMS_TEST_MODE=True`)

### Starting Redis & Celery (Optional)

```bash
# Start Redis (install separately)
redis-server

# Start Celery worker
celery -A config worker --loglevel=info

# Start Celery beat (for scheduled tasks like repayment reminders)
celery -A config beat --loglevel=info
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | Required |
| `DEBUG` | Debug mode | `False` |
| `DJANGO_SETTINGS_MODULE` | Settings module | `config.settings.production` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DATABASE_URL` | Database URL | Required for production |
| `DB_NAME` | PostgreSQL database name | - |
| `DB_USER` | PostgreSQL user | - |
| `DB_PASSWORD` | PostgreSQL password | - |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `REDIS_URL` | Redis URL | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker | `redis://localhost:6379/0` |
| `SAILUP_API_KEY` | Sailup API key | Required for real SMS |
| `SAILUP_BASE_URL` | Sailup API base URL | `https://api.sailup.io/v1` |
| `SAILUP_SENDER_ID` | SMS sender ID | `ZEMZEM` |
| `SAILUP_TIMEOUT` | Sailup request timeout | `10` |
| `SAILUP_ENABLED` | Enable real Sailup SMS | `False` |
| `SMS_TEST_MODE` | Log SMS instead of sending | `True` |
| `EMAIL_HOST` | SMTP host | `smtp.gmail.com` |
| `EMAIL_PORT` | SMTP port | `587` |
| `TIME_ZONE` | Timezone | `Africa/Accra` |

## Sailup SMS Configuration

### Setup

1. Create a Sailup account at https://www.sailup.io and get an API key (prefix `sailup_`).
2. Register your sender ID (e.g. `ZEMZEM`) in the Sailup dashboard.
3. Set environment variables:

```env
SAILUP_API_KEY=sailup_your-api-key-here
SAILUP_BASE_URL=https://api.sailup.io/v1
SAILUP_SENDER_ID=ZEMZEM
SAILUP_TIMEOUT=10
SAILUP_ENABLED=True
SMS_TEST_MODE=False
```

The provider abstraction (`apps/notifications/providers/`) keeps SMS provider-independent. Sailup is the default provider; another provider can be added later without rewriting the application.

### SMS Events

The system sends branded SMS from Zemzem Savings and Loans for:
- Contribution recorded (with updated savings balance)
- Withdrawal request/approval/rejection
- Loan application submitted
- Loan approved/rejected
- Loan disbursed
- Loan repayment recorded
- Repayment reminders (via Celery beat)
- OTP/security verification

SMS is always sent asynchronously via Celery after the financial transaction is saved. If Sailup is unavailable, the financial transaction still succeeds.

### Safe Testing

Set `SMS_TEST_MODE=True` (or leave `SAILUP_ENABLED=False`) to log SMS instead of sending real messages. SMS records are still created in the database, allowing you to verify the notification flow without sending actual SMS. To send real test SMS locally, set a valid `SAILUP_API_KEY` with `SAILUP_ENABLED=True` and `SMS_TEST_MODE=False`.

## Render Deployment

### Build Command

```bash
bash build.sh
```

### Start Command

```bash
gunicorn config.wsgi:application
```

### Celery Worker (Separate Service)

```bash
celery -A config worker --loglevel=info
```

### Environment Variables for Render

Set all required environment variables in the Render dashboard (Settings → Environment). Key variables:
- `SECRET_KEY`
- `DATABASE_URL` (use Render PostgreSQL)
- `REDIS_URL` (use Render Redis)
- `SAILUP_API_KEY`
- `SAILUP_BASE_URL`
- `SAILUP_SENDER_ID`
- `SAILUP_ENABLED=True`
- `SMS_TEST_MODE=False`
- `ALLOWED_HOSTS=your-app.onrender.com`
- `DJANGO_SETTINGS_MODULE=config.settings.production`

The `Procfile` starts a Celery worker and beat alongside the web process; SMS is delivered asynchronously by the worker.

## Database Migrations

### Development

```bash
python manage.py makemigrations
python manage.py migrate
```

### Production

```bash
python manage.py migrate
```

Never run `makemigrations` in production. Always create migrations during development, commit them, and apply in production.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_contributions.py -v

# Run with coverage
python -m pytest tests/ --cov=apps
```

## Security

- CSRF protection enabled
- XSS protection headers
- SQL injection prevention (Django ORM)
- Secure password hashing (Django's PBKDF2)
- Role-based access control
- Object-level authorization for customer data
- Audit logging for all financial operations
- Environment variables for all secrets
- HTTPS ready (production settings)

## Financial Integrity

- **Immutable ledger** - All financial transactions are recorded with balance-before and balance-after
- **Atomic transactions** - Django `transaction.atomic()` for all financial operations
- **Concurrency safety** - `select_for_update()` for balance updates
- **Decimal arithmetic** - All monetary values use Python `Decimal`, never `float`
- **Idempotency** - Unique transaction references prevent duplicate processing
- **SMS decoupled** - Financial transactions succeed even if SMS fails

## License

This project is for educational and commercial use.
