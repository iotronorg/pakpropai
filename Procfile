web: gunicorn config.wsgi --workers 2 --timeout 60 --bind 0.0.0.0:$PORT
worker: celery -A config worker --loglevel=info --concurrency 2