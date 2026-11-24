"""
Store daily FX rates from a CSV with the header ``currency,per_usd`` (T-2041).

    python manage.py import_fx_rates rates.csv --as-of 2026-11-24 --source "State Bank"
"""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.markets.models import FxRate


class Command(BaseCommand):
    help = 'Import FX rates (units per 1 USD). All rows or none; a second import for the day replaces it.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--as-of', required=True)
        parser.add_argument('--source', required=True)

    def handle(self, *args, path, as_of, source, **options):
        try:
            day = date.fromisoformat(as_of)
        except ValueError:
            raise CommandError('--as-of must be YYYY-MM-DD') from None
        if day > timezone.now().date():
            raise CommandError('--as-of is in the future')
        try:
            with open(path, newline='', encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
        except OSError as exc:
            raise CommandError(f'Cannot read the file: {exc}') from None
        with transaction.atomic():
            for n, row in enumerate(rows, 2):
                code = (row.get('currency') or '').strip().upper()
                try:
                    rate = FxRate.objects.filter(currency=code, as_of=day).first() or FxRate(currency=code, as_of=day)
                    rate.per_usd, rate.source = Decimal((row.get('per_usd') or '').strip()), source
                    rate.full_clean()
                except (InvalidOperation, ValidationError) as exc:
                    raise CommandError(f'Line {n}: {exc}') from None
                rate.save()
        self.stdout.write(f'{len(rows)} rates stored for {day}.')
