import json

from django.core.management.base import BaseCommand

from apps.locations.matching import link_unmatched, report


class Command(BaseCommand):
    help = ('T-1060a: link listings without a location_ref to the location hierarchy '
            '(creates unverified nodes for unknown places = admin review queue), then print coverage.')

    def add_arguments(self, parser):
        parser.add_argument('--report-only', action='store_true')
        parser.add_argument('--limit', type=int, default=None)
        parser.add_argument('--batch-size', type=int, default=500)

    def handle(self, *args, report_only=False, limit=None, batch_size=500, **opts):
        if not report_only:
            stats = link_unmatched(batch_size=batch_size, limit=limit)
            self.stdout.write(self.style.SUCCESS(f"checked {stats['checked']}, linked {stats['linked']}"))
        self.stdout.write(json.dumps(report(), indent=2, default=str))
