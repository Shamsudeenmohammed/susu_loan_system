#!/bin/bash
# Build script for production deployment (Render, Railway, etc.)

set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
