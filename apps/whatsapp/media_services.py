"""
WhatsApp Media Downloader — authenticated download + optional S3 archival.

Flow:
  1. Exchange media_id for a transient signed URL (WhatsApp Cloud API)
  2. Stream the binary payload with a size cap
  3. Validate MIME type and payload integrity
  4. Upload to S3-compatible storage when configured (boto3)
"""
import io
import logging
from dataclasses import dataclass

import requests
from django.conf import settings

from apps.whatsapp.client import WA_API_URL

logger = logging.getLogger(__name__)

_MAX_MEDIA_BYTES = 16 * 1024 * 1024  # WhatsApp platform cap

_SUPPORTED_MIMES: frozenset = frozenset({
    # Audio
    'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/amr',
    'audio/wav', 'audio/aac', 'audio/opus',
    # Image
    'image/jpeg', 'image/png', 'image/webp',
    # Document (images that arrive as documents)
    'application/pdf',
})


# ── Typed exceptions ───────────────────────────────────────────────────────────

class MediaError(Exception):
    """Base for all media-pipeline errors."""


class MediaAuthError(MediaError):
    """WA Cloud API returned 401/403 — token invalid or expired."""


class MediaNotFoundError(MediaError):
    """WA Cloud API returned 404 — media_id has expired or is unknown."""


class MediaRateLimitError(MediaError):
    """WA Cloud API returned 429 — caller must back off and retry."""


class MediaSizeError(MediaError):
    """Downloaded payload exceeds the platform size cap."""


class MediaCorruptedError(MediaError):
    """Downloaded payload is empty or has an unsupported MIME type."""


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class MediaDownloadResult:
    data: bytes
    mime_type: str
    size_bytes: int
    cdn_url: str = ''    # Cloudinary secure_url, empty when Cloudinary not configured
    media_id: str = ''


# ── Downloader ─────────────────────────────────────────────────────────────────

class WhatsAppMediaDownloader:
    """
    Two-step downloader matching the WhatsApp Cloud API media flow.
    All methods are classmethods — instantiation is not needed.
    """

    @classmethod
    def download(cls, media_id: str, mime_type: str) -> MediaDownloadResult:
        """
        Download media from WhatsApp and (optionally) upload to S3.

        Raises:
            MediaAuthError      — 401/403 from WA API
            MediaNotFoundError  — 404 (media_id expired or unknown)
            MediaRateLimitError — 429 (caller should retry with backoff)
            MediaSizeError      — payload exceeds 16 MB cap
            MediaCorruptedError — empty payload or unsupported MIME type
            MediaError          — network / unexpected failures
        """
        token = cls._token()
        url   = cls._resolve_url(media_id, token)
        data  = cls._fetch_bytes(url, token)
        cls._validate(data, mime_type)

        cdn_url = ''
        if cls._cloudinary_configured():
            try:
                cdn_url = cls._upload_to_cloudinary(data, media_id, mime_type)
                logger.info("Cloudinary archived media_id=%s url=%s", media_id, cdn_url)
            except Exception as exc:
                # Cloudinary failures are non-fatal: pipeline continues with in-memory bytes.
                logger.warning("Cloudinary upload failed for media_id=%s: %s", media_id, exc)

        return MediaDownloadResult(
            data=data,
            mime_type=mime_type,
            size_bytes=len(data),
            cdn_url=cdn_url,
            media_id=media_id,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _token() -> str:
        from apps.config.services import SystemConfigService
        return SystemConfigService.get('wa_access_token')

    @classmethod
    def _resolve_url(cls, media_id: str, token: str) -> str:
        """Step 1: exchange media_id → transient signed URL."""
        try:
            resp = requests.get(
                f"{WA_API_URL}/{media_id}",
                headers={'Authorization': f'Bearer {token}'},
                timeout=10,
            )
        except requests.exceptions.Timeout:
            raise MediaError(f"Timeout resolving media_id={media_id}")
        except requests.exceptions.RequestException as exc:
            raise MediaError(f"Network error resolving media_id={media_id}: {exc}")

        if resp.status_code == 401:
            raise MediaAuthError("WhatsApp token is invalid or expired.")
        if resp.status_code == 403:
            raise MediaAuthError(f"Forbidden — cannot resolve media_id={media_id}.")
        if resp.status_code == 404:
            raise MediaNotFoundError(
                f"media_id={media_id} not found. WhatsApp media URLs expire after ~5 minutes."
            )
        if resp.status_code == 429:
            raise MediaRateLimitError(
                "WhatsApp API rate limit hit — back off before retrying."
            )
        try:
            resp.raise_for_status()
            return resp.json()['url']
        except (KeyError, ValueError) as exc:
            raise MediaCorruptedError(
                f"Unexpected media info response body: {resp.text[:200]}"
            ) from exc

    @classmethod
    def _fetch_bytes(cls, url: str, token: str) -> bytes:
        """Step 2: stream binary payload from the signed URL with a size guard."""
        try:
            resp = requests.get(
                url,
                headers={'Authorization': f'Bearer {token}'},
                timeout=30,
                stream=True,
            )
        except requests.exceptions.Timeout:
            raise MediaError("Timeout downloading media binary.")
        except requests.exceptions.RequestException as exc:
            raise MediaError(f"Network error downloading media: {exc}")

        if resp.status_code == 401:
            raise MediaAuthError("Token rejected while downloading media binary.")
        if resp.status_code == 429:
            raise MediaRateLimitError("Rate limit hit while downloading media binary.")
        resp.raise_for_status()

        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > _MAX_MEDIA_BYTES:
                raise MediaSizeError(
                    f"Media payload exceeds {_MAX_MEDIA_BYTES // (1024 * 1024)} MB cap."
                )
            chunks.append(chunk)
        return b''.join(chunks)

    @staticmethod
    def _validate(data: bytes, mime_type: str) -> None:
        if not data:
            raise MediaCorruptedError("Downloaded media payload is empty.")
        if mime_type not in _SUPPORTED_MIMES:
            raise MediaCorruptedError(
                f"Unsupported MIME type: {mime_type!r}. "
                f"Accepted: {sorted(_SUPPORTED_MIMES)}"
            )

    @staticmethod
    def _cloudinary_configured() -> bool:
        cfg = getattr(settings, 'CLOUDINARY_STORAGE', {})
        return bool(cfg.get('CLOUD_NAME') and cfg.get('API_KEY') and cfg.get('API_SECRET'))

    @classmethod
    def _upload_to_cloudinary(cls, data: bytes, media_id: str, mime_type: str) -> str:
        """
        Upload binary to Cloudinary and return the secure CDN URL.

        Cloudinary resource_type mapping:
          audio/* → 'video'  (Cloudinary stores audio under the video type)
          image/* → 'image'
          application/pdf and other documents → 'raw'
        """
        import cloudinary.uploader

        category      = mime_type.split('/')[0]
        resource_type = {'audio': 'video', 'image': 'image'}.get(category, 'raw')

        try:
            result = cloudinary.uploader.upload(
                io.BytesIO(data),
                folder='wa-media',
                public_id=media_id,
                resource_type=resource_type,
                overwrite=True,
            )
        except Exception as exc:
            raise MediaError(f"Cloudinary upload failed: {exc}") from exc

        return result.get('secure_url', '')
