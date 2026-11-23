import json

from django.core.management.base import BaseCommand, CommandError

from apps.properties.listing_contract import problems, status


class Command(BaseCommand):
    help = ('T-1070g gate: verify the legacy listing columns (area_*, property_type) can be '
            'dropped (g-1 applied, mirror trigger present, zero drift). Exits non-zero otherwise. '
            'Run before deploying the g-2 migrations; they run the same check and refuse.')

    def handle(self, *args, **opts):
        self.stdout.write(json.dumps(status(), indent=2))
        found = problems()
        if found:
            raise CommandError('Not safe to drop the v1 listing columns:\n- ' + '\n- '.join(found))
        self.stdout.write(self.style.SUCCESS('OK: the g-2 contract migrations can be applied.'))
