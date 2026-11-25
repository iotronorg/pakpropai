"""MVP acceptance 4: how fast enquiry notifications reached agents, per channel (ADR-050)."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.slo import ENQUIRY_TARGET_SECONDS, delivery_report


class Command(BaseCommand):
    help = ('Delivery of enquiry notifications against the one-minute target, per channel. '
            'Exit code 1 with --fail-under-objective when a channel misses the objective.')

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=float, default=24.0, help='Window length, ending now (default 24)')
        parser.add_argument('--event-prefix', default='enquiry.',
                            help='Notification events to include (default "enquiry.")')
        parser.add_argument('--target', type=float, default=ENQUIRY_TARGET_SECONDS,
                            help='Target in seconds (default 60)')
        parser.add_argument('--json', action='store_true', help='Print JSON')
        parser.add_argument('--fail-under-objective', action='store_true',
                            help='Exit 1 when a channel with data is under the objective')

    def handle(self, *args, **opts):
        if opts['hours'] <= 0 or opts['target'] <= 0:
            raise CommandError('--hours and --target must be positive.')
        since = timezone.now() - timedelta(hours=opts['hours'])
        report = delivery_report(since, event_prefix=opts['event_prefix'], target=opts['target'])
        if opts['json']:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self.stdout.write(f"Notifications '{report['event_prefix']}*' since {report['since']} "
                              f"(target {report['target_s']:g} s, objective {report['objective']:.0%})")
            for c in report['channels']:
                ratio = '-' if c['ratio_within_target'] is None else f"{c['ratio_within_target']:.2%}"
                self.stdout.write(
                    f"  {c['channel']:<8} total={c['total']} delivered={c['delivered']} "
                    f"within={c['within_target']} ({ratio}) late_pending={c['late_pending']} "
                    f"pending={c['pending']} failed={c['failed']} "
                    f"p50={_s(c['p50_s'])} p95={_s(c['p95_s'])} max={_s(c['max_s'])}")
            for note in report['notes']:
                self.stdout.write(self.style.WARNING(note))
        missed = [c['channel'] for c in report['channels'] if c['meets_objective'] is False]
        if missed and opts['fail_under_objective']:
            raise CommandError(f"Under the objective: {', '.join(missed)}")


def _s(value):
    return '-' if value is None else f'{value:g}s'
