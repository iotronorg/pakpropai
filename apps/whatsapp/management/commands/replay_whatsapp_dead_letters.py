"""Re-enqueue inbound WhatsApp messages parked in the Redis dead-letter list (T-1020)."""
from django.core.management.base import BaseCommand

from apps.whatsapp.inbound_resilience import dead_letter_count, replay_dead_letters


class Command(BaseCommand):
    help = 'Re-enqueue dead-lettered inbound WhatsApp messages (oldest first). Run after the DB has recovered.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='Maximum number of messages to replay.')
        parser.add_argument('--count', action='store_true', help='Only print the number of parked messages.')

    def handle(self, *args, **opts):
        if opts['count']:
            self.stdout.write(str(dead_letter_count()))
            return
        replayed = replay_dead_letters(limit=opts['limit'])
        self.stdout.write(self.style.SUCCESS(f'Re-enqueued {replayed} message(s); {dead_letter_count()} remaining.'))
