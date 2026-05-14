web: gunicorn config.wsgi --workers 2 --timeout 60 --bind 0.0.0.0:$PORT
worker: celery -A config worker --loglevel=info --concurrency 2
beat: celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
