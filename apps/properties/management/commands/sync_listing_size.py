from django.core.management.base import BaseCommand

from apps.properties.size_sync import count_drift, sync_v1_to_v2


class Command(BaseCommand):
    help = ('T-1070b: copy deprecated area_* size columns into size_* where they differ. '
            'Run once after every pre-v2 pod has stopped (GO_LIVE_CHECKLIST §14).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report the count.')

    def handle(self, *args, dry_run=False, **opts):
        drift = count_drift()
        if dry_run:
            self.stdout.write(f'{drift} listing(s) out of sync')
            return
        updated = sync_v1_to_v2()
        self.stdout.write(self.style.SUCCESS(f'synced {updated} listing(s)'))
