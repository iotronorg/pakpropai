"""T-4050: hash photos that have no dHash yet, then rebuild duplicate clusters (run once after deploy)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Compute missing photo hashes (dHash) and rebuild listing duplicate clusters.'

    def add_arguments(self, parser):
        parser.add_argument('--skip-images', action='store_true', help='Only rebuild the clusters.')
        parser.add_argument('--limit', type=int, default=0, help='At most this many photos to hash.')

    def handle(self, *args, **opts):
        from PIL import Image

        from apps.core.images import dhash
        from apps.properties.duplicates import rebuild
        from apps.properties.models import PropertyImage
        hashed = failed = 0
        if not opts['skip_images']:
            qs = PropertyImage.objects.filter(dhash='').exclude(sha256='').order_by('created_at')
            if opts['limit']:
                qs = qs[:opts['limit']]
            for img in qs.iterator(chunk_size=200):
                try:
                    with img.image.open('rb') as f, Image.open(f) as pic:
                        PropertyImage.objects.filter(pk=img.pk).update(dhash=dhash(pic))
                    hashed += 1
                except Exception as exc:                     # missing file, storage outage
                    failed += 1
                    self.stderr.write(f'image {img.pk}: {exc.__class__.__name__}')
        n = rebuild()
        self.stdout.write(f'hashed={hashed} failed={failed} listings_checked={n}')
