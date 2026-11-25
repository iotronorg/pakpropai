"""
Deleting a person's files at Cloudinary during erasure (KI-105, owner decision 2026-11-25).

Report PDFs, property-audit PDFs and WhatsApp media were uploaded with
``cloudinary.uploader.upload`` and only their URL was kept. Erasure deletes them
by the public id read back from that URL. "Not found" counts as deleted, so a
repeated erasure is harmless; any other answer or an API error raises, which
fails the erasure step and makes the hourly job try again (ADR-049).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

_HOSTS = ('res.cloudinary.com',)
_VERSION = re.compile(r'^v\d+$')
_TYPES = ('image', 'video', 'raw')


class CloudFileError(Exception):
    """A file could not be deleted (the message never contains the URL)."""


def parse(url: str) -> tuple[str, str] | None:
    """``(resource_type, public_id)`` for one of our Cloudinary delivery URLs, else None."""
    try:
        u = urlparse(url or '')
    except ValueError:
        return None
    if u.hostname not in _HOSTS:
        return None
    parts = [unquote(p) for p in u.path.split('/') if p]
    cloud = settings.CLOUDINARY_STORAGE.get('CLOUD_NAME') or ''
    # /<cloud>/<resource_type>/upload/[transformations/][v123/]<public_id>
    if len(parts) < 4 or parts[1] not in _TYPES or parts[2] != 'upload' or (cloud and parts[0] != cloud):
        return None
    rest = parts[3:]
    for i, p in enumerate(rest):
        if _VERSION.match(p):
            rest = rest[i + 1:]
            break
    if not rest:
        return None
    return parts[1], '/'.join(rest)


def _candidates(resource_type: str, public_id: str) -> list[str]:
    """Raw ids keep their extension; image and video ids do not."""
    stem, dot, _ = public_id.rpartition('.')
    if not dot or '/' in public_id[len(stem):]:
        return [public_id]
    return [public_id, stem] if resource_type == 'raw' else [stem, public_id]


def delete(url: str) -> bool:
    """Delete one file. True when it is gone (or was never there); raises CloudFileError otherwise."""
    target = parse(url)
    if target is None:
        return False
    conf = settings.CLOUDINARY_STORAGE
    if not (conf.get('CLOUD_NAME') and conf.get('API_KEY') and conf.get('API_SECRET')):
        raise CloudFileError('Cloudinary credentials are not configured')
    import cloudinary.uploader
    resource_type, public_id = target
    for pid in _candidates(resource_type, public_id):
        try:
            result = cloudinary.uploader.destroy(
                pid, resource_type=resource_type, invalidate=True,
                cloud_name=conf['CLOUD_NAME'], api_key=conf['API_KEY'], api_secret=conf['API_SECRET'])
        except Exception as exc:
            raise CloudFileError(f'destroy failed: {type(exc).__name__}') from None
        answer = (result or {}).get('result')
        if answer == 'ok':
            return True
        if answer != 'not found':
            raise CloudFileError(f'unexpected answer: {answer!r}')
    return True


def delete_all(urls) -> int:
    """Delete every Cloudinary file among ``urls``; returns how many were ours."""
    n = 0
    for url in dict.fromkeys(u for u in urls if u):
        if delete(url):
            n += 1
    if n:
        logger.info('erasure.cloud_files_deleted count=%s', n)
    return n
