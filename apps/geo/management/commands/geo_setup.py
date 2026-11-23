from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.geo.search import reset_ready_cache
from apps.geo.sql import GIST_INDEX, GIST_INDEX_SQL


class Command(BaseCommand):
    help = ('T-1060b: enable PostGIS geo search: CREATE EXTENSION postgis and build the GiST '
            'index on the listing public point (CONCURRENTLY). Idempotent. Needs a DB role that '
            'may create extensions. Then set GEO_POSTGIS_ENABLED=true. --check only reports.')

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')

    def handle(self, *args, check=False, **opts):
        with connection.cursor() as cur:
            cur.execute("SELECT default_version, installed_version FROM pg_available_extensions "
                        "WHERE name = 'postgis'")
            row = cur.fetchone()
            cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname = %s", [GIST_INDEX])
            has_index = cur.fetchone()[0] == 1
        available, installed = (row is not None), (row is not None and row[1] is not None)
        self.stdout.write(f'postgis available={available} installed={installed} '
                          f'gist_index={has_index}')
        if check:
            return
        if not available:
            raise CommandError('PostGIS is not available on this database server; '
                               'install it (or use a PostGIS-enabled managed DB) first.')
        concurrently = '' if connection.in_atomic_block else 'CONCURRENTLY'
        with connection.cursor() as cur:
            cur.execute('CREATE EXTENSION IF NOT EXISTS postgis')
            cur.execute(GIST_INDEX_SQL.format(concurrently=concurrently))
        reset_ready_cache()
        self.stdout.write(self.style.SUCCESS(
            'PostGIS ready. Set GEO_POSTGIS_ENABLED=true to use it for geo search.'))
