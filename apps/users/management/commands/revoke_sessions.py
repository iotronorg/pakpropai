"""Sign people out by blacklisting their refresh tokens (runbook: data incident)."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = ('Blacklist refresh tokens: --all, or --user PHONE (repeatable). Access tokens stay valid until they '
            'expire (ACCESS_TOKEN_LIFETIME, 15 minutes); rotate SECRET_KEY to end those at once.')

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument('--all', action='store_true')
        target.add_argument('--user', action='append', metavar='PHONE')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **o):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

        live = OutstandingToken.objects.filter(expires_at__gt=timezone.now(), blacklistedtoken__isnull=True)
        if o['user']:
            users = list(get_user_model().objects.filter(phone__in=o['user']))
            if len(users) != len(set(o['user'])):
                raise CommandError('Unknown phone number(s).')
            live = live.filter(user__in=users)
        tokens = list(live.only('id'))
        if not o['dry_run']:
            BlacklistedToken.objects.bulk_create([BlacklistedToken(token=t) for t in tokens], ignore_conflicts=True)
        self.stdout.write(f"{len(tokens)} refresh tokens {'would be ' if o['dry_run'] else ''}revoked.")
