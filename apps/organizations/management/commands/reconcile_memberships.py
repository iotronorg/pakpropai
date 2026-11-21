"""
Report (and optionally repair) drift between OrganizationMembership and its legacy
projections Organization.admin_user / Agent.organization (ADR-013).

    manage.py reconcile_memberships                 # report only
    manage.py reconcile_memberships --apply         # repair missing owner/agent rows
    manage.py reconcile_memberships --fail-on-drift # exit 1 if anything is reported
"""
from django.core.management.base import BaseCommand, CommandError

from apps.organizations.membership import REPAIRABLE, REPORT_ONLY, find_drift, repair_drift


class Command(BaseCommand):
    help = 'Report/repair drift between organization memberships and Agent/Organization links.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Create/reactivate missing owner and agent memberships.')
        parser.add_argument('--show', type=int, default=20,
                            help='Max (user_id, org_id) pairs printed per category.')
        parser.add_argument('--fail-on-drift', action='store_true',
                            help='Exit with status 1 when any category is non-empty.')

    def handle(self, *args, **opts):
        drift = find_drift()
        for key in REPAIRABLE + REPORT_ONLY:
            rows = drift[key]
            tag = 'repairable' if key in REPAIRABLE else 'report only'
            self.stdout.write(f'{key} ({tag}): {len(rows)}')
            for user_id, org_id in rows[:opts['show']]:
                self.stdout.write(f'  user={user_id} org={org_id}')

        if opts['apply']:
            fixed = repair_drift(drift)
            self.stdout.write(self.style.SUCCESS(
                'repaired: ' + ', '.join(f'{k}={v}' for k, v in fixed.items())))
            drift = find_drift()

        if opts['fail_on_drift'] and any(drift[k] for k in REPAIRABLE + REPORT_ONLY):
            raise CommandError('membership drift detected')
