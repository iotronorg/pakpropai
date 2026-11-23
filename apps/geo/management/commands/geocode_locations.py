import json

from django.core.management.base import BaseCommand, CommandError

from apps.geo.geocoding import budget_used, due_locations, get_geocoder, query_for, run


class Command(BaseCommand):
    help = ('T-1060c: geocode location centroids with Google (needs GEOCODING_ENABLED and '
            'GOOGLE_MAPS_API_KEY; counts against GEOCODING_DAILY_BUDGET). Listings without '
            'coordinates then use their location centroid (snapped). --dry-run lists queries only.')

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=200)
        parser.add_argument('--market', default=None)
        parser.add_argument('--retry-failed', action='store_true',
                            help='Also retry zero / rejected / error results.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, limit, market, retry_failed, dry_run, **opts):
        due = due_locations(market, retry_failed)
        if dry_run:
            self.stdout.write(f'{due.count()} location(s) due; first {min(limit, 20)}:')
            for loc in due[:min(limit, 20)]:
                self.stdout.write(f'  {loc.path}  →  "{query_for(loc)}"')
            return
        if get_geocoder() is None:
            raise CommandError('Geocoding is disabled: set GEOCODING_ENABLED=true and GOOGLE_MAPS_API_KEY.')
        stats = run(limit=limit, market=market, retry_failed=retry_failed)
        stats['budget_used_today'] = budget_used()
        self.stdout.write(json.dumps(stats, indent=2))
