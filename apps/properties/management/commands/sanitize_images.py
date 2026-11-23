"""
T-1110 backfill: re-encode images uploaded before the sanitiser existed, so
their EXIF/GPS metadata and client file names are gone.

* Property images with sha256 = '' (the pre-T-1110 marker): re-encoded, a
  thumbnail generated, the old file deleted after the row points at the new one.
* --include-profiles: also agent profile photos and organisation logos (always
  re-encoded; idempotent apart from new file names).

Files that fail the sanitiser are reported and left untouched (review them in
admin). --dry-run only counts.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.images import ImageRejected, sanitize_image

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Re-encode pre-T-1110 uploaded images (strip metadata, safe names, thumbnails).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)
        parser.add_argument('--include-profiles', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, limit, include_profiles, dry_run, **opts):
        from apps.properties.models import PropertyImage
        stats = {'done': 0, 'rejected': 0, 'missing': 0}
        qs = PropertyImage.objects.filter(sha256='').order_by('created_at')
        self.stdout.write(f'{qs.count()} property image(s) to process')
        if dry_run:
            return
        for img in qs[:limit]:
            stats[self._property_image(img)] += 1
        if include_profiles:
            from apps.agents.models import Agent
            from apps.organizations.models import Organization
            for obj, field in ([(a, 'profile_photo') for a in Agent.objects.exclude(profile_photo='')
                                .exclude(profile_photo__isnull=True)]
                               + [(o, 'logo') for o in Organization.objects.exclude(logo='')
                                  .exclude(logo__isnull=True)]):
                stats[self._single(obj, field)] += 1
        self.stdout.write(str(stats))

    def _read(self, fieldfile):
        try:
            fieldfile.open('rb')
            return fieldfile
        except (FileNotFoundError, OSError):
            return None

    def _property_image(self, img):
        src = self._read(img.image)
        if src is None:
            return 'missing'
        try:
            clean = sanitize_image(src, thumbnail_side=settings.IMAGE_THUMB_SIDE)
        except ImageRejected as exc:
            logger.warning('sanitize_images: property image %s rejected: %s', img.pk, exc)
            return 'rejected'
        finally:
            src.close()
        old_name = img.image.name
        img.image.save(clean.content.name, clean.content, save=False)
        img.thumbnail.save(clean.thumbnail.name, clean.thumbnail, save=False)
        img.width, img.height, img.size_bytes, img.sha256 = clean.width, clean.height, clean.size, clean.sha256
        img.save(update_fields=['image', 'thumbnail', 'width', 'height', 'size_bytes', 'sha256'])
        img.image.storage.delete(old_name)
        return 'done'

    def _single(self, obj, field):
        ff = getattr(obj, field)
        src = self._read(ff)
        if src is None:
            return 'missing'
        try:
            clean = sanitize_image(src, max_side=1024)
        except ImageRejected as exc:
            logger.warning('sanitize_images: %s %s rejected: %s', type(obj).__name__, obj.pk, exc)
            return 'rejected'
        finally:
            src.close()
        old_name = ff.name
        ff.save(clean.content.name, clean.content, save=False)
        type(obj).objects.filter(pk=obj.pk).update(**{field: ff.name})
        ff.storage.delete(old_name)
        return 'done'
