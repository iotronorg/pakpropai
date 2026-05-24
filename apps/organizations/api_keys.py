"""
ApiKeyManager — cryptographic API token generation and verification.

Token format:  rtk_<prefix12>_<secret48>
  rtk_       — literal namespace marker
  prefix12   — 12 hex chars (6 random bytes); stored in DB for indexed lookup; safe to display
  secret48   — 48 base64url chars (36 random bytes); never stored; only its PBKDF2 hash is

Storage:
  key_prefix — plaintext prefix (indexed, looked up on every auth call)
  key_hash   — PBKDF2-SHA256(secret, salt, iterations=260_000) as hex string
  key_salt   — per-key random salt (32 bytes as hex)

Security properties:
  • PBKDF2-SHA256 with 260 000 iterations meets NIST SP 800-132 guidance for 2024
  • Constant-time comparison via hmac.compare_digest prevents timing attacks
  • Per-key random salt prevents rainbow-table and batch cracking
  • last_used_at writes are rate-limited to one DB write per 5 min per key via Redis
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security.api_keys')

_TOKEN_PREFIX  = 'rtk'
_PBKDF2_ALGO   = 'sha256'
_PBKDF2_ITERS  = 260_000
_LAST_USED_TTL = 300  # seconds — throttle DB writes


class ApiKeyManager:

    # ── Generation ─────────────────────────────────────────────────────────────

    @classmethod
    def generate(
        cls,
        organization,
        name: str,
        scopes: list[str],
        expires_at=None,
    ) -> tuple[str, 'DeveloperApiKey']:
        """
        Create a new API key for *organization*.

        Returns (raw_token, DeveloperApiKey instance).
        raw_token is shown to the developer exactly once — it is not recoverable.

        Raises ValueError if any scope is not in DeveloperApiKey.VALID_SCOPES.
        """
        from apps.organizations.models import DeveloperApiKey

        invalid = set(scopes) - DeveloperApiKey.VALID_SCOPES
        if invalid:
            raise ValueError(f"Invalid scopes: {invalid}. Valid: {DeveloperApiKey.VALID_SCOPES}")

        prefix = cls._unique_prefix()
        secret, salt_hex, key_hash = cls._make_secret()
        raw_token = f"{_TOKEN_PREFIX}_{prefix}_{secret}"

        instance = DeveloperApiKey.objects.create(
            organization=organization,
            name=name,
            key_prefix=prefix,
            key_hash=key_hash,
            key_salt=salt_hex,
            scopes=list(scopes),
            expires_at=expires_at,
        )

        security_logger.info(
            'API_KEY_CREATED: org=%s key_id=%s prefix=%s scopes=%s',
            organization.pk, instance.pk, prefix, scopes,
        )
        return raw_token, instance

    # ── Verification ───────────────────────────────────────────────────────────

    @classmethod
    def authenticate(cls, raw_token: str) -> 'DeveloperApiKey | None':
        """
        Authenticate a raw token string.

        Returns the DeveloperApiKey on success, None on any failure.
        Never raises — all failures are swallowed and logged at DEBUG level.
        """
        from apps.organizations.models import DeveloperApiKey
        from django.utils import timezone

        parsed = cls._parse(raw_token)
        if parsed is None:
            return None
        prefix, secret = parsed

        try:
            instance = (
                DeveloperApiKey.objects
                .select_related('organization')
                .get(key_prefix=prefix, is_active=True)
            )
        except DeveloperApiKey.DoesNotExist:
            # Constant-time dummy work to prevent prefix-based timing oracle
            cls._dummy_verify()
            return None

        expected_hash = cls._compute_hash(secret, instance.key_salt)
        if not hmac.compare_digest(expected_hash, instance.key_hash):
            security_logger.warning(
                'API_KEY_BAD_SECRET: prefix=%s org=%s — hash mismatch',
                prefix, instance.organization_id,
            )
            return None

        if instance.expires_at and timezone.now() > instance.expires_at:
            security_logger.info(
                'API_KEY_EXPIRED: key_id=%s org=%s', instance.pk, instance.organization_id,
            )
            return None

        cls._touch_last_used(instance)
        return instance

    @classmethod
    def verify(cls, raw_token: str, instance: 'DeveloperApiKey') -> bool:
        """Direct verification against a known instance (e.g. for testing)."""
        parsed = cls._parse(raw_token)
        if not parsed:
            return False
        prefix, secret = parsed
        if prefix != instance.key_prefix:
            return False
        expected = cls._compute_hash(secret, instance.key_salt)
        return hmac.compare_digest(expected, instance.key_hash)

    # ── Internals ──────────────────────────────────────────────────────────────

    @classmethod
    def _parse(cls, raw_token: str) -> tuple[str, str] | None:
        parts = raw_token.split('_', 2)
        if len(parts) != 3 or parts[0] != _TOKEN_PREFIX:
            return None
        prefix, secret = parts[1], parts[2]
        if len(prefix) != 12 or len(secret) < 40:
            return None
        return prefix, secret

    @classmethod
    def _make_secret(cls) -> tuple[str, str, str]:
        """Returns (secret_str, salt_hex, key_hash)."""
        secret_bytes = os.urandom(36)
        secret = base64.urlsafe_b64encode(secret_bytes).decode().rstrip('=')[:48]
        salt   = os.urandom(32)
        salt_hex  = salt.hex()
        key_hash  = cls._compute_hash(secret, salt_hex)
        return secret, salt_hex, key_hash

    @classmethod
    def _compute_hash(cls, secret: str, salt_hex: str) -> str:
        salt = bytes.fromhex(salt_hex)
        return hashlib.pbkdf2_hmac(
            _PBKDF2_ALGO, secret.encode(), salt, _PBKDF2_ITERS
        ).hex()

    @classmethod
    def _dummy_verify(cls) -> None:
        """Constant-time work executed when the prefix has no DB match."""
        _, salt_hex, _ = cls._make_secret()
        cls._compute_hash(secrets.token_hex(48), salt_hex)

    @classmethod
    def _unique_prefix(cls) -> str:
        """Generate a prefix guaranteed to be globally unique in the DB."""
        from apps.organizations.models import DeveloperApiKey
        for _ in range(5):
            candidate = secrets.token_hex(6)  # 12 hex chars
            if not DeveloperApiKey.objects.filter(key_prefix=candidate).exists():
                return candidate
        raise RuntimeError("Could not generate a unique API key prefix after 5 attempts.")

    @classmethod
    def _touch_last_used(cls, instance: 'DeveloperApiKey') -> None:
        from django.core.cache import cache
        from django.utils import timezone
        cache_key = f'api_key_last_used:{instance.pk}'
        if cache.get(cache_key):
            return
        try:
            instance.last_used_at = timezone.now()
            instance.save(update_fields=['last_used_at'])
            cache.set(cache_key, 1, _LAST_USED_TTL)
        except Exception:
            pass  # never block the request
