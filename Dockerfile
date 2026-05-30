FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.dev

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/base.txt
COPY requirements/prod.txt requirements/prod.txt
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .

EXPOSE 8000

# Dev image: runserver via docker-compose.yml command override.
# collectstatic is intentionally NOT run at build time — it needs SECRET_KEY
# and DJANGO_SETTINGS_MODULE to be set from the runtime env, not baked in.
# For production use docker/Dockerfile.web instead (entrypoint handles it).
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
