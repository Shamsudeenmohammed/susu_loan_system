import os
from dotenv import load_dotenv
from celery import Celery

load_dotenv()

# Default to production settings so the Celery worker (web + worker processes on
# Render) uses the same environment as the WSGI app. Local/development runs
# override this via DJANGO_SETTINGS_MODULE in .env.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

app = Celery('susu_loan_system')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
