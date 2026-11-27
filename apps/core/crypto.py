"""
Field-level encryption for secrets and national IDs (ADR-008).

* Fernet (AES-128-CBC + HMAC-SHA256) via ``cryptography``; MultiFernet for key
  rotation. Keys come from ``settings.FIELD_ENCRYPTION_KEYS`` (env
  ``FIELD_ENCRYPTION_KEYS``, comma-separated, newest first).
* Stored format: ``enc1:<fernet token>``. Values without the prefix are legacy
  plaintext and are returned unchanged, so reads keep working while existing
  rows are migrated.
* Dev/test may derive a key from SECRET_KEY (``FIELD_ENCRYPTION_ALLOW_DERIVED_KEY``).
  Production without a key fails closed (system check error + exception on use).
* ``blind_index()`` gives a deterministic HMAC for equality lookups on encrypted
  columns (key: ``FIELD_HASH_KEY`` or derived from the primary encryption key).

Losing the keys makes encrypted data unrecoverable — keep them in the secret
manager with a backup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

PREFIX = 'enc1:'


def _derived_key() -> bytes:
    digest = hashlib.sha256(f'field-encryption:{settings.SECRET_KEY}'.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def configured_keys() -> list[bytes]:
    raw = getattr(settings, 'FIELD_ENCRYPTION_KEYS', None) or []
    if isinstance(raw, str):
        raw = [k for k in raw.split(',')]
    keys = [k.strip().encode() for k in raw if k and k.strip()]
    if keys:
        return keys
    if getattr(settings, 'FIELD_ENCRYPTION_ALLOW_DERIVED_KEY', False):
        return [_derived_key()]
    return []


@lru_cache(maxsize=4)
def _fernet_for(keys: tuple[bytes, ...]):
    from cryptography.fernet import Fernet, MultiFernet
    return MultiFernet([Fernet(k) for k in keys])


def _fernet():
    keys = configured_keys()
    if not keys:
        raise ImproperlyConfigured(
            'FIELD_ENCRYPTION_KEYS is not set. Generate one with '
            '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.'
        )
    return _fernet_for(tuple(keys))


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_str(value):
    """Encrypt a string. None/'' and already-encrypted values pass through."""
    if value is None or value == '' or is_encrypted(value):
        return value
    token = _fernet().encrypt(str(value).encode()).decode()
    return PREFIX + token


def decrypt_str(value):
    """Decrypt an ``enc1:`` value; anything else (legacy plaintext) is returned as-is."""
    if not is_encrypted(value):
        return value
    return _fernet().decrypt(value[len(PREFIX):].encode()).decode()


def rotate_str(value):
    """Re-encrypt under the newest key (for key rotation jobs)."""
    if not is_encrypted(value):
        return encrypt_str(value)
    return PREFIX + _fernet().rotate(value[len(PREFIX):].encode()).decode()


def blind_index(value) -> str:
    """Deterministic HMAC-SHA256 hex digest for equality lookups ('' for empty)."""
    if not value:
        return ''
    key = getattr(settings, 'FIELD_HASH_KEY', '') or ''
    key_bytes = key.encode() if key else hashlib.sha256(b'blind-index:' + _fernet_primary_key()).digest()
    return hmac.new(key_bytes, str(value).encode(), hashlib.sha256).hexdigest()


def _fernet_primary_key() -> bytes:
    keys = configured_keys()
    if not keys:
        _fernet()  # raises ImproperlyConfigured
    return keys[0]


class EncryptedTextField(models.TextField):
    """
    TextField whose value is encrypted at rest. Transparent in Python code.

    Equality lookups (``filter(field=x)``) cannot match — ciphertext is randomised.
    Use a separate blind-index column for lookups.
    """
    description = 'Encrypted text'

    def from_db_value(self, value, expression, connection):
        return decrypt_str(value)

    def to_python(self, value):
        return decrypt_str(super().to_python(value))

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_str(value)


class EncryptedJSONField(models.JSONField):
    """JSON encrypted at rest (a text column). No JSON lookups."""
    description = 'Encrypted JSON'

    def get_internal_type(self):
        return 'TextField'         # migrations cast between jsonb and text

    def db_type(self, connection):
        return 'text'

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return json.loads(decrypt_str(value), cls=self.decoder)

    def get_db_prep_value(self, value, connection, prepared=False):
        if value is None:
            return None
        return encrypt_str(json.dumps(value, cls=self.encoder))


# ── Migration helpers ─────────────────────────────────────────────────────────

def _rewrite_columns(schema_editor, table: str, pk: str, columns: list[str], transform):
    conn = schema_editor.connection
    qn = schema_editor.quote_name
    cols = ', '.join(qn(c) for c in columns)
    with conn.cursor() as cur:
        cur.execute(f'SELECT {qn(pk)}, {cols} FROM {qn(table)}')  # nosec B608 - only constants or quoted identifiers are interpolated; values are parameters
        rows = cur.fetchall()
        for row in rows:
            pk_val, values = row[0], row[1:]
            new_values = [transform(v) for v in values]
            if list(new_values) == list(values):
                continue
            sets = ', '.join(f'{qn(c)} = %s' for c in columns)
            cur.execute(f'UPDATE {qn(table)} SET {sets} WHERE {qn(pk)} = %s', [*new_values, pk_val])  # nosec B608 - only constants or quoted identifiers are interpolated; values are parameters


def encrypt_columns(table: str, columns: list[str], pk: str = 'id'):
    """RunPython forward op: encrypt existing plaintext values in place."""
    def forward(apps, schema_editor):
        _rewrite_columns(schema_editor, table, pk, columns, encrypt_str)
    return forward


def decrypt_columns(table: str, columns: list[str], pk: str = 'id'):
    """RunPython reverse op (recovery path): restore plaintext."""
    def backward(apps, schema_editor):
        _rewrite_columns(schema_editor, table, pk, columns, decrypt_str)
    return backward
