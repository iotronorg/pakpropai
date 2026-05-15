# PakProp AI — Backend API

Django REST API powering PakProp AI — the trust infrastructure layer for Pakistani real estate.

WhatsApp-first · AI-powered · Modular Django monolith

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Running Locally with Docker](#running-locally-with-docker)
- [Environment Variables Reference](#environment-variables-reference)
- [Useful Commands](#useful-commands)
- [Deploying to Production](#deploying-to-production)
  - [Option A — Render (recommended, one-click)](#option-a--render-recommended)
  - [Option B — Railway](#option-b--railway)
  - [Option C — Any VPS with Docker](#option-c--any-vps-with-docker)
- [Post-Deployment Checklist](#post-deployment-checklist)

---

## Prerequisites

For local development you only need:

| Tool | Version | Install |
|---|---|---|
| Docker Desktop | 24+ | https://docs.docker.com/get-docker/ |
| Docker Compose | v2 (bundled with Docker Desktop) | included |

You do **not** need Python, PostgreSQL, or Redis installed locally — Docker provides everything.

---

## Running Locally with Docker

### 1. Clone and enter the directory

```bash
git clone <your-repo-url>
cd pakpropai
```

### 2. Create your `.env` file

Copy the example and fill in the minimum required values:

```bash
cp .env.example .env
```

Open `.env` and set at least these:

```env
SECRET_KEY=any-long-random-string-for-local-dev
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgresql://pakprop:pakprop@db:5432/pakpropai
REDIS_URL=redis://redis:6379/0
```

> The database host is `db` and Redis host is `redis` — these are Docker service names.
> Do **not** use `localhost` for these when running inside Docker Compose.

### 3. Start all services

```bash
docker compose up --build
```

This starts 5 services in dependency order:

| Service | Role | Local Port |
|---|---|---|
| `db` | PostgreSQL 15 | 5432 |
| `redis` | Redis 7 | 6379 |
| `web` | Django dev server (auto-migrates on start) | 8000 |
| `worker` | Celery task worker | — |
| `beat` | Celery periodic task scheduler | — |

Wait for this line before using the API:

```
web-1  | Starting development server at http://0.0.0.0:8000/
```

### 4. Verify it's running

```bash
curl http://localhost:8000/health/
# expected: {"status": "ok"}
```

Django admin panel: http://localhost:8000/admin/

### 5. Create a superuser (first time only)

```bash
docker compose exec web python manage.py shell -c "
from apps.users.models import User
User.objects.create_superuser(phone='+923001234567', password='admin123')
"
```

### 6. Stop everything

```bash
docker compose down          # stop containers, keep volumes (data preserved)
docker compose down -v       # stop containers AND delete all data (clean slate)
```

---

## Environment Variables Reference

### Required for all environments

```env
SECRET_KEY=           # Long random string — NEVER share or commit this
DEBUG=                # True for local | False for production
ALLOWED_HOSTS=        # Comma-separated list: localhost,127.0.0.1 (local) or yourdomain.com (prod)
DATABASE_URL=         # Full PostgreSQL URL e.g. postgresql://user:pass@host:5432/dbname
REDIS_URL=            # Full Redis URL e.g. redis://localhost:6379/0
```

### WhatsApp Cloud API

```env
WA_VERIFY_TOKEN=      # Any string you choose — entered in Meta webhook setup
WA_APP_SECRET=        # Meta App Dashboard → Your App → App Secret
WA_ACCESS_TOKEN=      # Meta WhatsApp → API Setup → Permanent Token
WA_PHONE_NUMBER_ID=   # Meta WhatsApp → API Setup → Phone Number ID
WA_OTP_TEMPLATE_NAME= # Name of your approved OTP message template in Meta
```

> All WhatsApp features are disabled gracefully if these are not set.

### Gemini AI

```env
GEMINI_API_KEY=       # From Google AI Studio: aistudio.google.com
GEMINI_MODEL=gemini-2.5-flash-lite    # Keep this for free tier
AI_BACKEND=gemini     # gemini (cloud) | local (Ollama, for fully offline dev)
```

### Cloudflare R2 (file storage)

```env
R2_ACCOUNT_ID=        # Cloudflare dashboard → Account ID (right sidebar)
R2_ACCESS_KEY_ID=     # Cloudflare R2 → Manage API Tokens → Create Token
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=       # Name of your R2 bucket
R2_PUBLIC_URL=        # https://pub-xxx.r2.dev (if bucket has public access enabled)
```

> If not set, files are stored on local disk under `media/`. Fine for development.

### Payment Gateways

```env
SAFEPAY_MERCHANT_KEY=    # From Safepay dashboard
SAFEPAY_SECRET_KEY=
SAFEPAY_ENVIRONMENT=sandbox    # sandbox | production

BSECURE_CLIENT_ID=       # From bSecure dashboard
BSECURE_CLIENT_SECRET=
BSECURE_ENVIRONMENT=sandbox    # sandbox | production
```

### Production only

```env
FRONTEND_URL=         # e.g. https://app.pakpropai.com — controls CORS and CSRF
BASE_URL=             # e.g. https://api.pakpropai.com — used in PDF/report download links
SENTRY_DSN=           # Optional — from sentry.io for error tracking and alerting
```

---

## Useful Commands

Run these via `docker compose exec web` when containers are up:

```bash
# Apply database migrations
docker compose exec web python manage.py migrate

# Open the Django interactive shell
docker compose exec web python manage.py shell

# Run the test suite
docker compose exec web python manage.py test

# Check for configuration errors
docker compose exec web python manage.py check

# Collect static files (done automatically in Dockerfile for prod)
docker compose exec web python manage.py collectstatic --noinput

# Follow Celery worker logs
docker compose logs worker -f

# Follow Celery beat scheduler logs
docker compose logs beat -f

# Rebuild images after changing requirements
docker compose up --build
```

---

## Deploying to Production

---

### Option A — Render (recommended)

The repo includes `render.yaml` — a Blueprint file that provisions everything in one click: web service, Celery worker, Celery beat, PostgreSQL, and Redis.

#### Steps

**1. Push your code to GitHub or GitLab.**

**2. Go to Render → New → Blueprint → Connect your repository.**

Render detects `render.yaml` automatically and shows a preview of what will be created:

- `pakpropai-api` — Django web service (Docker)
- `pakpropai-worker` — Celery worker (Docker)
- `pakpropai-beat` — Celery beat scheduler (Docker)
- `pakpropai-db` — PostgreSQL database (free tier)
- Redis — (free tier)

**3. Click Apply. Render builds and deploys all services.**

**4. Set the secret environment variables.**

Go to each service → Environment and fill in the values marked `sync: false` in `render.yaml`. These are the secrets not committed to the repo:

```
WA_VERIFY_TOKEN
WA_APP_SECRET
WA_ACCESS_TOKEN
WA_PHONE_NUMBER_ID
WA_OTP_TEMPLATE_NAME
GEMINI_API_KEY
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
R2_PUBLIC_URL
SAFEPAY_MERCHANT_KEY
SAFEPAY_SECRET_KEY
BSECURE_CLIENT_ID
BSECURE_CLIENT_SECRET
SENTRY_DSN               (optional)
```

**5. Update `ALLOWED_HOSTS` and `FRONTEND_URL`** to your actual domain names in the web service environment.

**6. Trigger a manual redeploy** from the Render dashboard after adding secrets.

Migrations run automatically as part of the start command — no manual step needed.

#### Cost on Render

| Tier | Web service | Databases | Monthly |
|---|---|---|---|
| Free | Spins down after 15min inactivity | Free (limited) | $0 |
| Starter | Always-on | Managed PostgreSQL | ~$14/month |

---

### Option B — Railway

Railway uses the `Procfile` to start all three processes (web, worker, beat).

#### Steps

**1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub.**

**2. Select your repository.**

**3. Add plugins from the Railway dashboard:**
- Click **+ New** → **Database** → **PostgreSQL**
- Click **+ New** → **Database** → **Redis**

Railway injects `DATABASE_URL` and `REDIS_URL` into your service automatically.

**4. Set environment variables** (your service → Variables tab):

```env
DJANGO_SETTINGS_MODULE=config.settings.prod
SECRET_KEY=<generate a strong random key>
ALLOWED_HOSTS=<your-project>.up.railway.app
FRONTEND_URL=https://<your-frontend-domain>
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
BASE_URL=https://<your-project>.up.railway.app

# Then add all WhatsApp, Gemini, R2, and payment variables
```

**5. Railway reads your `Procfile` and starts:**

```
web:    gunicorn config.wsgi --workers 2 --timeout 60 --bind 0.0.0.0:$PORT
worker: celery -A config worker --loglevel=info --concurrency 2
beat:   celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

**6. Click Deploy.**

#### Cost on Railway

Starter plan: ~$5/month for the first service. Worker and beat each count as additional services.

---

### Option C — Any VPS with Docker

Use this for DigitalOcean Droplets, Hetzner Cloud, AWS EC2, or any Linux server.

#### 1. Install Docker on the server

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

#### 2. Clone the repository

```bash
git clone <your-repo-url> /opt/pakpropai
cd /opt/pakpropai
```

#### 3. Create the production `.env` file

```bash
cp .env.example .env
nano .env
```

Fill in all values. Important production settings:

```env
SECRET_KEY=<strong-random-key>
DEBUG=False
ALLOWED_HOSTS=api.yourdomain.com
DJANGO_SETTINGS_MODULE=config.settings.prod
DATABASE_URL=postgresql://pakprop:<DB_PASSWORD>@db:5432/pakpropai
REDIS_URL=redis://redis:6379/0
FRONTEND_URL=https://app.yourdomain.com
BASE_URL=https://api.yourdomain.com
# ... all other required vars
```

#### 4. Create `docker-compose.prod.yml`

```yaml
services:
  db:
    image: postgres:15-alpine
    restart: always
    environment:
      POSTGRES_DB: pakpropai
      POSTGRES_USER: pakprop
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pakprop -d pakpropai"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: always
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  web:
    build: .
    restart: always
    command: >
      sh -c "python manage.py migrate --noinput &&
             gunicorn config.wsgi --workers 2 --timeout 60 --bind 0.0.0.0:8000"
    volumes:
      - media_files:/app/media
    ports:
      - "127.0.0.1:8000:8000"
    env_file: .env
    environment:
      DJANGO_SETTINGS_MODULE: config.settings.prod
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    restart: always
    command: celery -A config worker --loglevel=info --concurrency 2
    volumes:
      - media_files:/app/media
    env_file: .env
    environment:
      DJANGO_SETTINGS_MODULE: config.settings.prod
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  beat:
    build: .
    restart: always
    command: >
      celery -A config beat --loglevel=info
      --scheduler django_celery_beat.schedulers:DatabaseScheduler
    env_file: .env
    environment:
      DJANGO_SETTINGS_MODULE: config.settings.prod
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  postgres_data:
  media_files:
```

#### 5. Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

#### 6. Set up Nginx as a reverse proxy

Install Nginx: `sudo apt install nginx`

Create `/etc/nginx/sites-available/pakpropai-api`:

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/pakpropai-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 7. Get a free SSL certificate

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com
```

Certbot auto-renews. Test renewal with: `sudo certbot renew --dry-run`

#### 8. View logs

```bash
docker compose -f docker-compose.prod.yml logs web -f
docker compose -f docker-compose.prod.yml logs worker -f
```

#### 9. Update to a new version

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Migrations run automatically on restart.

---

## Post-Deployment Checklist

Complete these steps after your first production deploy:

**1. Create the admin user**

```bash
# Render/Railway: use their shell/console feature in the dashboard
# VPS:
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "
from apps.users.models import User
User.objects.create_superuser(phone='+923001234567', password='your-secure-password')
"
```

**2. Verify deployment health**

```bash
curl https://api.yourdomain.com/health/
# expected: {"status": "ok"}

curl https://api.yourdomain.com/api/v1/config/
# should return system configuration (after logging in as admin)
```

**3. Configure system settings via the Admin Dashboard**

Log in to the frontend as admin → **Settings**:
- Enter WhatsApp credentials
- Enter Gemini API key
- Select active payment gateway
- Enable feature flags
- Verify the `setup_complete` field shows `true`

**4. Register the WhatsApp webhook with Meta**

Meta Business Manager → WhatsApp → Configuration → Webhook:

- **Callback URL:** `https://api.yourdomain.com/api/v1/whatsapp/webhook/`
- **Verify Token:** the value you set in `WA_VERIFY_TOKEN`
- **Subscribe to:** `messages`

Click Verify — Meta sends a GET request to your webhook URL. It must return the challenge value, which the backend handles automatically.

**5. Create the OTP message template in Meta**

Meta Business Manager → WhatsApp → Message Templates → Create:

- **Category:** Authentication
- **Language:** English (or Urdu)
- **Name:** (must match `WA_OTP_TEMPLATE_NAME` in your env)
- **Body:** `Your PakProp AI code is {{1}}. Expires in 5 minutes.`

Wait for Meta approval — typically 1 to 24 hours.

**6. Test end-to-end**

```bash
# Test OTP (delivers via WhatsApp if configured, or returns error if not)
curl -X POST https://api.yourdomain.com/api/v1/auth/otp/send/ \
  -H "Content-Type: application/json" \
  -d '{"phone": "+923001234567"}'
```
