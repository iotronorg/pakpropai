"""
Image upload sanitiser (T-1110, ADR-027). Every user-uploaded image goes through
:func:`sanitize_image` before it reaches storage.

1. Size: at most ``max_bytes`` are read; anything larger is rejected.
2. Format from the bytes (magic numbers), never from the client's
   Content-Type or file name: JPEG, PNG or WebP only.
3. Decode with Pillow under a pixel budget (``IMAGE_MAX_PIXELS``) checked from
   the header *before* decoding (decompression bombs), then a full decode.
4. Apply the EXIF orientation, then **re-encode** from pixels: EXIF (GPS,
   camera serial, timestamps), XMP, comments and any appended payload are
   gone. Only the colour profile is kept. Animated images keep the first frame.
5. Downscale to ``IMAGE_MAX_SIDE``; optional thumbnail.
6. The stored name is ``<uuid4>.<ext of the detected format>``: user input
   never reaches the storage key.
"""
from __future__ import annotations

import hashlib
import io
import uuid
import warnings
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile

_MAGIC = (
    (b'\xff\xd8\xff', 'JPEG'),
    (b'\x89PNG\r\n\x1a\n', 'PNG'),
)
_EXT = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}
CONTENT_TYPES = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}


class ImageRejected(ValueError):
    """The upload is not an acceptable image (message is safe to show)."""


@dataclass
class CleanImage:
    content: ContentFile          # .name = '<uuid>.<ext>'
    format: str                   # JPEG / PNG / WEBP
    width: int
    height: int
    size: int
    sha256: str
    thumbnail: ContentFile | None = None
    dhash: str = ''                 # 64-bit difference hash, 16 hex (T-4050 duplicate detection)

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES[self.format]


def sniff(head: bytes) -> str | None:
    for magic, fmt in _MAGIC:
        if head.startswith(magic):
            return fmt
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    return None


def _read_limited(upload, max_bytes: int) -> bytes:
    if hasattr(upload, 'seek'):
        try:
            upload.seek(0)
        except (OSError, ValueError):
            pass
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageRejected(f'File too large (max {max_bytes // (1024 * 1024)} MB).')
    if not data:
        raise ImageRejected('Empty file.')
    return data


def _normalise_mode(img, fmt: str):
    has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
    if fmt == 'JPEG':
        return img.convert('RGB') if img.mode != 'RGB' else img
    if has_alpha:
        return img.convert('RGBA') if img.mode != 'RGBA' else img
    return img.convert('RGB') if img.mode != 'RGB' else img


def _encode(img, fmt: str, icc: bytes | None) -> bytes:
    out = io.BytesIO()
    kw = {'icc_profile': icc} if icc else {}
    if fmt == 'JPEG':
        img.save(out, 'JPEG', quality=85, optimize=True, progressive=True, **kw)
    elif fmt == 'PNG':
        img.save(out, 'PNG', optimize=True, **kw)
    else:
        img.save(out, 'WEBP', quality=85, method=4, **kw)
    return out.getvalue()


def dhash(img) -> str:
    """64-bit difference hash of a PIL image: survives resizing and recompression (T-4050)."""
    from PIL import Image
    g = img.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
    px = g.tobytes()                                   # 72 grey levels, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f'{bits:016x}'


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count('1')


def sanitize_image(upload, *, max_bytes: int | None = None, max_side: int | None = None,
                   thumbnail_side: int | None = None) -> CleanImage:
    from PIL import Image, ImageOps, UnidentifiedImageError

    max_bytes = max_bytes or settings.IMAGE_MAX_BYTES
    max_side = max_side or settings.IMAGE_MAX_SIDE
    data = _read_limited(upload, max_bytes)
    fmt = sniff(data[:16])
    if fmt is None:
        raise ImageRejected('Unsupported file type. Use JPEG, PNG or WebP.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format != fmt:
                raise ImageRejected('File content does not match its image type.')
            w, h = probe.size
            if w < 1 or h < 1 or w * h > settings.IMAGE_MAX_PIXELS:
                raise ImageRejected('Image dimensions are too large.')
            probe.verify()                                   # structure / CRC checks
            img = Image.open(io.BytesIO(data))
            img.seek(0)                                      # first frame of an animation
            icc = img.info.get('icc_profile')
            img = ImageOps.exif_transpose(img)               # keep the visible orientation
            img.load()
    except ImageRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected('Image dimensions are too large.') from None
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning,
            OSError, SyntaxError, ValueError, EOFError):
        raise ImageRejected('The file is not a valid image.') from None

    img = _normalise_mode(img, fmt)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    clean = Image.new(img.mode, img.size)                    # pixels only: no info/exif carried over
    clean.paste(img)
    body = _encode(clean, fmt, icc)
    stem = uuid.uuid4().hex
    thumb = None
    if thumbnail_side:
        t = clean.copy()
        t.thumbnail((thumbnail_side, thumbnail_side), Image.Resampling.LANCZOS)
        thumb = ContentFile(_encode(t, fmt, icc), name=f'{stem}_t.{_EXT[fmt]}')
    return CleanImage(content=ContentFile(body, name=f'{stem}.{_EXT[fmt]}'), format=fmt,
                      width=clean.width, height=clean.height, size=len(body),
                      sha256=hashlib.sha256(body).hexdigest(), thumbnail=thumb, dhash=dhash(clean))


def safe_upload_name(prefix: str, filename: str) -> str:
    """Storage key for ``upload_to``: never trusts the incoming extension."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in _EXT.values():
        ext = 'bin'
    return f'{prefix}/{uuid.uuid4().hex}.{ext}'


def _drf_field():
    from rest_framework import serializers

    class SanitizedImageField(serializers.ImageField):
        """ImageField whose validated value is the sanitised, renamed image (T-1110)."""

        def __init__(self, *args, max_side: int | None = None, **kwargs):
            self._max_side = max_side
            super().__init__(*args, **kwargs)

        def to_internal_value(self, data):
            upload = serializers.FileField.to_internal_value(self, data)   # skip Django's Pillow pass
            try:
                return sanitize_image(upload, max_side=self._max_side).content
            except ImageRejected as exc:
                raise serializers.ValidationError(str(exc)) from None

    return SanitizedImageField


SanitizedImageField = _drf_field()
