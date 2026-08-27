web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3
worker: celery -A config worker --loglevel=info --concurrency=4
beat: celery -A config beat --loglevel=info
