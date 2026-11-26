"""``manage.py backfill_contact_messages [--batch N] [--limit N] [--check]`` (T-3100b, ADR-064)."""
from django.core.management.base import BaseCommand, CommandError

from apps.leads import backfill


class Command(BaseCommand):
    help = 'Copy the old WhatsApp log into the message log (idempotent, in batches).'

    def add_arguments(self, parser):
        parser.add_argument('--batch', type=int, default=500)
        parser.add_argument('--limit', type=int, default=0, help='Stop after this many rows (0: all)')
        parser.add_argument('--check', action='store_true', help='Only count; exit 1 while rows remain')

    def handle(self, *args, batch, limit, check, **opts):
        if batch < 1:
            raise CommandError('--batch must be at least 1')
        if check:
            left = backfill.pending().count()
            self.stdout.write(f'pending={left} without_id={backfill.unmatched()}')
            if left:
                raise CommandError(f'{left} WhatsApp messages are not in the message log yet')
            return
        total = {'linked': 0, 'copied': 0}
        while True:
            size = batch if not limit else min(batch, limit - total['linked'] - total['copied'])
            if size <= 0:
                break
            out = backfill.run_batch(size)
            total['linked'] += out['linked']
            total['copied'] += out['copied']
            if out['batch'] < size:
                break
        self.stdout.write(f"linked={total['linked']} copied={total['copied']} "
                          f"pending={backfill.pending().count()} without_id={backfill.unmatched()}")
