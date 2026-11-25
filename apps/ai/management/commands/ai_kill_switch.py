"""Stop or restart the assistant from a shell, e.g. when the API is down (T-2070)."""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.ai import kill_switch
from apps.ai.models import AIKillSwitch


class Command(BaseCommand):
    help = 'ai_kill_switch list | stop --reason TEXT (--platform | --market PK | --org UUID) | release ID'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['list', 'stop', 'release'])
        parser.add_argument('id', nargs='?')
        target = parser.add_mutually_exclusive_group()
        target.add_argument('--platform', action='store_true')
        target.add_argument('--market')
        target.add_argument('--org')
        parser.add_argument('--reason', default='')

    def handle(self, *args, **o):
        if o['action'] == 'stop':
            self._stop(o)
        elif o['action'] == 'release':
            try:
                kill_switch.release(AIKillSwitch.objects.get(pk=o['id']))
            except (AIKillSwitch.DoesNotExist, ValidationError) as exc:
                raise CommandError(f'Unknown stop: {o["id"]}') from exc
            except kill_switch.AlreadyReleased as exc:
                raise CommandError(str(exc)) from exc
        for s in AIKillSwitch.objects.filter(released_at__isnull=True):
            self.stdout.write(f'{s.pk}  {s}  since {s.created_at:%Y-%m-%d %H:%M}  {s.reason}')

    def _stop(self, o):
        from apps.markets.models import Market
        from apps.organizations.models import Organization
        if not o['reason']:
            raise CommandError('--reason is required.')
        if not (o['platform'] or o['market'] or o['org']):
            raise CommandError('Name a target: --platform, --market or --org.')
        try:
            market = Market.objects.get(pk=o['market'].upper()) if o['market'] else None
            org = Organization.objects.get(pk=o['org']) if o['org'] else None
        except (Market.DoesNotExist, Organization.DoesNotExist, ValidationError) as exc:
            raise CommandError('Unknown market or organization.') from exc
        try:
            kill_switch.stop(reason=o['reason'][:300], market=market, organization=org)
        except kill_switch.AlreadyStopped as exc:
            raise CommandError(str(exc)) from exc
