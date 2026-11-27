"""
T-6040: is this build and configuration safe to release?

    python manage.py check_release                       # ENVIRONMENT from settings
    python manage.py check_release --environment staging
    python manage.py check_release --json                # machine-readable
    python manage.py check_release --strict              # warnings fail too

Exit code 1 when a check fails (or warns, with --strict). Output names settings, never their values.
The deploy runs it after migrating and before the new containers start (remote_deploy.sh).
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.core.release_checks import ENVIRONMENTS, FAIL, OK, WARN, run_checks


class Command(BaseCommand):
    help = 'Check that frozen modules are off and required production settings are present (T-6040).'
    # Django's checks would stop at the first error; run_checks() runs them and reports everything.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--environment', choices=ENVIRONMENTS,
                            help='Override settings.ENVIRONMENT (frozen modules fail only in production).')
        parser.add_argument('--json', action='store_true', help='Print the findings as JSON.')
        parser.add_argument('--strict', action='store_true', help='Fail on warnings too.')
        parser.add_argument('--verbose-ok', action='store_true', help='Also list the checks that passed.')

    def handle(self, *args, **opts):
        findings = run_checks(opts['environment'])
        fails = [f for f in findings if f.level == FAIL]
        warns = [f for f in findings if f.level == WARN]
        passed = len(findings) - len(fails) - len(warns)

        if opts['json']:
            self.stdout.write(json.dumps({
                'ok': not fails and not (opts['strict'] and warns),
                'failed': len(fails), 'warnings': len(warns), 'passed': passed,
                'findings': [f.as_dict() for f in findings if opts['verbose_ok'] or f.level != OK],
            }, indent=2))
        else:
            for f in fails:
                self.stdout.write(self.style.ERROR(f'FAIL  {f.code}: {f.message}'))
            for f in warns:
                self.stdout.write(self.style.WARNING(f'WARN  {f.code}: {f.message}'))
            if opts['verbose_ok']:
                for f in findings:
                    if f.level == OK:
                        self.stdout.write(f'ok    {f.code}')
            self.stdout.write(f'release check: {len(fails)} failed, {len(warns)} warnings, {passed} passed')

        if fails or (opts['strict'] and warns):
            raise CommandError('release check failed' + ('' if fails else ' (warnings, --strict)'))
