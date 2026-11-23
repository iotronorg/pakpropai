from django.core.management.base import BaseCommand

from apps.properties.taxonomy import count_drift, sync_property_type_to_category


class Command(BaseCommand):
    help = ('T-1070d: derive category from the deprecated property_type where they disagree '
            '(clears subtype on those rows). Run once after every pre-v2 pod has stopped '
            '(GO_LIVE_CHECKLIST §16).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report the count.')

    def handle(self, *args, dry_run=False, **opts):
        drift = count_drift()
        if dry_run:
            self.stdout.write(f'{drift} listing(s) out of sync')
            return
        updated = sync_property_type_to_category()
        self.stdout.write(self.style.SUCCESS(f'synced {updated} listing(s)'))
