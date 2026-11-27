"""
Release checks (T-6040, MVP_SCOPE §7 "Feature flags verified: frozen modules off in production").

``run_checks(environment)`` returns a list of ``Finding``. ``fail`` blocks a deploy, ``warn`` is printed
for the operator, and ``ok`` records what was checked. Findings never contain secret values: they
name the setting and say what is wrong with it.

The deploy (``scripts/deploy/remote_deploy.sh``) runs ``manage.py check_release`` after the migrations
and before the new containers start. Staging may switch frozen modules on to test them, so there they
are warnings; in production they fail.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from django.conf import settings

FAIL, WARN, OK = 'fail', 'warn', 'ok'
ENVIRONMENTS = ('production', 'staging')

# MVP_SCOPE §4.2: regulated, licensing or cost risk. Key → what it unfreezes.
FROZEN_FLAGS = {
    'feature_deal_lock':      'deal lock / escrow token payments',
    'feature_voice_calls':    'Twilio voice calls',
    'feature_syndication':    'syndication marketplace',
    'scraper_search_enabled': 'third-party portal scraping',
}
# Off by default and need a decision before they go on (legal wording, ADR-042).
REVIEW_FLAGS = {
    'feature_investment_commentary': 'AI investment commentary (disclaimer needs legal sign-off)',
    'semantic_cache_enabled':        'shared answer cache (ADR-042)',
}
REGIONAL_DATABASES = {'eu_cluster': 'EU_DATABASE_URL', 'uk_cluster': 'UK_DATABASE_URL',
                      'uae_cluster': 'UAE_DATABASE_URL'}
# Placeholders from .env.example, CI and scripts/dev/test.sh.
_PLACEHOLDER_SECRET_MARKERS = ('change-me', 'not-for-production', 'django-insecure-')


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str

    def as_dict(self):
        return asdict(self)


def _on(value) -> bool:
    # The app treats only 'true' as on; anything else that is not plainly off is reported too.
    return str(value).strip().lower() not in ('', 'false', '0', 'no', 'off')


def _is_local_url(url: str) -> bool:
    host = (urlsplit(url).hostname or '').lower()
    return host in ('', 'localhost', '127.0.0.1', '::1', '0.0.0.0') or host.endswith('.local')  # nosec B104 - compared, not bound


# ── frozen modules ─────────────────────────────────────────────────────────────

def check_frozen_flags(environment: str) -> list[Finding]:
    """Platform flags read from the database (the source of truth; the cache lags up to a minute)."""
    from apps.config.models import SystemConfig
    from apps.organizations.models import OrganizationConfig

    keys = [*FROZEN_FLAGS, *REVIEW_FLAGS]
    rows = dict(SystemConfig.objects.filter(key__in=keys).values_list('key', 'value'))
    level = FAIL if environment == 'production' else WARN
    out = []
    for key, what in FROZEN_FLAGS.items():
        value = rows.get(key, SystemConfig.DEFAULTS.get(key, 'false'))
        if _on(value):
            out.append(Finding(level, f'frozen.{key}', f'{key} is on: {what} is frozen for the MVP '
                                                       '(MVP_SCOPE §4.2). Set it to false.'))
        else:
            out.append(Finding(OK, f'frozen.{key}', f'{key} is off'))
    for key, what in REVIEW_FLAGS.items():
        value = rows.get(key, SystemConfig.DEFAULTS.get(key, 'false'))
        if _on(value):
            out.append(Finding(WARN, f'review.{key}', f'{key} is on: {what}. Confirm this was decided.'))

    # Organization rows cannot unfreeze anything today (the gates read the platform value), but a
    # row that says "on" misleads the org's admins and would take effect if a gate ever read it.
    org_rows = (OrganizationConfig.objects.filter(key__in=list(FROZEN_FLAGS))
                .values_list('key', 'value'))
    counts: dict[str, int] = {}
    for key, value in org_rows:
        if _on(value):
            counts[key] = counts.get(key, 0) + 1
    for key, n in sorted(counts.items()):
        out.append(Finding(WARN, f'frozen.org.{key}',
                           f'{n} organization override(s) set {key} on; the platform value wins. '
                           'Reset them in the admin.'))
    return out


def check_dormant_modules() -> list[Finding]:
    from apps.inventory.models import ExternalPlatformConnection as C

    out = []
    regional = [env for alias, env in REGIONAL_DATABASES.items() if alias in settings.DATABASES]
    if regional:
        out.append(Finding(WARN, 'dormant.regional_db',
                           f'{", ".join(regional)} set: regional clusters are dormant for the MVP (MVP_SCOPE §4.2).'))
    # External portal feeds need a signed partner agreement (MVP_SCOPE §4.2); the model has no field
    # for it, so every active non-custom connection is listed for the operator to confirm.
    active = (C.objects.filter(is_active=True).exclude(platform=C.Platform.CUSTOM)
              .values_list('platform', flat=True))
    counts: dict[str, int] = {}
    for platform in active:
        counts[platform] = counts.get(platform, 0) + 1
    for platform, n in sorted(counts.items()):
        out.append(Finding(WARN, f'dormant.inventory.{platform}',
                           f'{n} active {platform} connection(s): confirm a signed partner agreement '
                           'or deactivate them.'))
    return out


# ── settings ───────────────────────────────────────────────────────────────────

def check_settings(environment: str) -> list[Finding]:
    s = settings
    out = []

    def need(cond, code, message, level=FAIL):
        out.append(Finding(OK if cond else level, code, 'ok' if cond else message))

    env_name = (getattr(s, 'ENVIRONMENT', '') or '').strip().lower()
    need(env_name in ENVIRONMENTS, 'settings.ENVIRONMENT',
         f'ENVIRONMENT must be production or staging (got {env_name or "nothing"}).')
    need(not s.DEBUG, 'settings.DEBUG', 'DEBUG is on.')
    need(os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.settings.prod', 'settings.module',
         'DJANGO_SETTINGS_MODULE is not config.settings.prod.')

    key = s.SECRET_KEY or ''
    need(len(key) >= 50 and len(set(key)) >= 5
         and not any(m in key.lower() for m in _PLACEHOLDER_SECRET_MARKERS),
         'settings.SECRET_KEY', 'SECRET_KEY is missing, short or a known placeholder (use 50+ random characters).')

    hosts = list(s.ALLOWED_HOSTS or [])
    need(hosts and '*' not in hosts, 'settings.ALLOWED_HOSTS', 'ALLOWED_HOSTS is empty or contains "*".')

    from apps.core.crypto import _fernet
    # Explicit keys only: a key derived from SECRET_KEY is dev/test only.
    valid = False
    if [k for k in (getattr(s, 'FIELD_ENCRYPTION_KEYS', None) or []) if str(k).strip()]:
        try:
            _fernet()
            valid = True
        except Exception:
            valid = False
    need(valid, 'settings.FIELD_ENCRYPTION_KEYS', 'FIELD_ENCRYPTION_KEYS is not set or is invalid.')
    need(not getattr(s, 'FIELD_ENCRYPTION_ALLOW_DERIVED_KEY', False), 'settings.FIELD_ENCRYPTION_ALLOW_DERIVED_KEY',
         'FIELD_ENCRYPTION_ALLOW_DERIVED_KEY is on (dev/test only).')
    need(bool(getattr(s, 'FIELD_HASH_KEY', '')), 'settings.FIELD_HASH_KEY',
         'FIELD_HASH_KEY is not set: lookup hashes derive from the encryption key and break when it rotates.')
    audit = getattr(s, 'AUDIT_CHAIN_KEY', '') or ''
    need(bool(audit) and audit != key, 'settings.AUDIT_CHAIN_KEY',
         'AUDIT_CHAIN_KEY is not set or equals SECRET_KEY (the audit chain would break on a SECRET_KEY change).')

    for name in ('FRONTEND_URL', 'BASE_URL'):
        url = getattr(s, name, '') or ''
        need(url.startswith('https://') and not _is_local_url(url), f'settings.{name}',
             f'{name} must be a public https:// address.')

    need(getattr(s, 'SECURE_SSL_REDIRECT', False) and getattr(s, 'SESSION_COOKIE_SECURE', False)
         and getattr(s, 'CSRF_COOKIE_SECURE', False) and getattr(s, 'SECURE_HSTS_SECONDS', 0) > 0,
         'settings.https', 'HTTPS redirect, secure cookies or HSTS are off.')

    token = getattr(s, 'PROMETHEUS_METRICS_TOKEN', '') or ''
    need(len(token) >= 16, 'settings.PROMETHEUS_METRICS_TOKEN',
         'PROMETHEUS_METRICS_TOKEN is unset or shorter than 16 characters: Prometheus cannot scrape /metrics/.')
    need('0.0.0.0' not in (getattr(s, 'PROMETHEUS_ALLOWED_IPS', []) or []),  # nosec B104 - compared, not bound
         'settings.PROMETHEUS_ALLOWED_IPS', 'PROMETHEUS_ALLOWED_IPS contains 0.0.0.0 (anyone can read /metrics/).')

    from apps.markets.registry import MARKET_REGISTRY
    need((getattr(s, 'DEFAULT_MARKET', '') or '').upper() in MARKET_REGISTRY, 'settings.DEFAULT_MARKET',
         'DEFAULT_MARKET has no entry in apps/markets/registry.py.')

    out.extend(_check_ai_providers())

    from apps.config.services import SystemConfigService
    missing = sorted(SystemConfigService.get_missing_required())
    need(not missing, 'config.required', f'Required platform settings are missing: {", ".join(missing)}.')

    if getattr(s, 'GEOCODING_ENABLED', False):
        need(bool(getattr(s, 'GOOGLE_MAPS_API_KEY', '')), 'settings.GOOGLE_MAPS_API_KEY',
             'GEOCODING_ENABLED is on without GOOGLE_MAPS_API_KEY.')

    # Not blocking: the platform works without them, but the operator should know.
    if getattr(s, 'OPENAPI_SCHEMA_PUBLIC', False) and environment == 'production':
        out.append(Finding(WARN, 'settings.OPENAPI_SCHEMA_PUBLIC', 'The API schema is public in production.'))
    if not getattr(s, 'EMAIL_HOST', ''):
        out.append(Finding(WARN, 'settings.EMAIL_HOST', 'EMAIL_HOST is not set: no email notifications.'))
    if not getattr(s, 'SENTRY_DSN', ''):
        out.append(Finding(WARN, 'settings.SENTRY_DSN', 'SENTRY_DSN is not set: errors are only in the logs.'))
    storage = getattr(s, 'CLOUDINARY_STORAGE', {}) or {}
    if not all(storage.get(k) for k in ('CLOUD_NAME', 'API_KEY', 'API_SECRET')):
        out.append(Finding(WARN, 'settings.CLOUDINARY', 'Cloudinary credentials are incomplete: media stays on local disk.'))
    if (getattr(s, 'APP_VERSION', '') or 'dev') == 'dev':
        out.append(Finding(WARN, 'settings.APP_VERSION', 'APP_VERSION is "dev": /health/ cannot report the build.'))
    return out


def _check_ai_providers() -> list[Finding]:
    from apps.ai.backends import PROVIDERS
    from apps.ai.llm import provider_order
    from apps.config.services import SystemConfigService

    s = settings
    out = []
    raw = getattr(s, 'AI_PROVIDER_ORDER', None) or [s.AI_BACKEND]
    unknown = [p for p in raw if p not in PROVIDERS]   # provider_order() drops these silently
    order = provider_order()
    if unknown:
        out.append(Finding(FAIL, 'ai.providers', f'Unknown AI providers in AI_PROVIDER_ORDER: {", ".join(unknown)}.'))
    for name in order:
        if name == 'gemini':
            ok = SystemConfigService.is_set('gemini_api_key')
            msg = 'gemini is an AI provider but GEMINI_API_KEY is not set.'
        elif name == 'openai':
            ok = bool(getattr(s, 'AI_OPENAI_API_KEY', '') and getattr(s, 'AI_OPENAI_MODEL', ''))
            msg = 'openai is an AI provider but AI_OPENAI_API_KEY or AI_OPENAI_MODEL is not set.'
        elif name == 'local':
            ok = not _is_local_url(getattr(s, 'OLLAMA_BASE_URL', '') or '')
            msg = 'local is an AI provider but OLLAMA_BASE_URL points at this container.'
        else:
            continue
        out.append(Finding(OK if ok else FAIL, f'ai.{name}', 'ok' if ok else msg))
    return out


# ── database and Django's own checks ───────────────────────────────────────────

def check_migrations() -> list[Finding]:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        names = ', '.join(f'{m.app_label} {m.name}' for m, _ in plan[:5])
        more = f' (+{len(plan) - 5} more)' if len(plan) > 5 else ''
        return [Finding(FAIL, 'db.migrations', f'{len(plan)} migration(s) not applied: {names}{more}.')]
    return [Finding(OK, 'db.migrations', 'all applied')]


def check_django_deploy() -> list[Finding]:
    from django.core import checks

    out = []
    for msg in checks.run_checks(include_deployment_checks=True):
        if msg.is_silenced():
            continue
        level = FAIL if msg.level >= checks.ERROR else WARN if msg.level >= checks.WARNING else None
        if level:
            out.append(Finding(level, f'django.{msg.id or "check"}', str(msg.msg)))
    return out


def run_checks(environment: str | None = None) -> list[Finding]:
    environment = (environment or getattr(settings, 'ENVIRONMENT', '') or '').strip().lower()
    groups = (
        ('settings', lambda: check_settings(environment)),
        ('frozen', lambda: check_frozen_flags(environment)),
        ('dormant', check_dormant_modules),
        ('db', check_migrations),
        ('django', check_django_deploy),
    )
    findings = []
    for name, run in groups:
        # One broken group (no database, undecryptable config) must not hide the others' results.
        try:
            findings.extend(run())
        except Exception as exc:
            findings.append(Finding(FAIL, f'{name}.error',
                                    f'The {name} checks could not run ({exc.__class__.__name__}).'))
    return findings
