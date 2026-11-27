"""T-6030: print the role × endpoint matrix (Markdown or JSON) for reviews and the pentest scope.

    python manage.py permission_matrix                # Markdown table, one row per route
    python manage.py permission_matrix --json
    python manage.py permission_matrix --audience public
"""
import json
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from apps.security.permission_matrix import AUDIENCE_ORDER, endpoints

ROLE_COLUMNS = ('anonymous', 'consumer', 'org agent', 'org admin', 'platform admin')
# audience → which of ROLE_COLUMNS get past the declared permission classes.
ALLOWED = {
    'public':    ROLE_COLUMNS,
    'signed_in': ROLE_COLUMNS[1:],
    'dashboard': ('org agent', 'org admin', 'platform admin'),
    'org_admin': ('org admin', 'platform admin'),
    'moderator': ('platform admin',),
    'staff':     ('platform admin',),
    'api_key':   (),
}


class Command(BaseCommand):
    help = 'Print every API endpoint with the roles its permission classes admit (T-6030).'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')
        parser.add_argument('--audience', choices=AUDIENCE_ORDER)

    def handle(self, *args, **opts):
        eps = [e for e in endpoints() if not opts['audience'] or e.audience == opts['audience']]
        routes = defaultdict(lambda: {'methods': [], 'audience': None, 'permissions': None})
        for e in eps:
            r = routes[e.template]
            r['methods'].append(e.method)
            # All methods of a route share one view, so they share its audience.
            r['audience'], r['permissions'] = e.audience, e.permissions
        if opts['json']:
            self.stdout.write(json.dumps({t: r for t, r in sorted(routes.items())}, indent=2))
            return
        counts = Counter(e.audience for e in eps)
        self.stdout.write('Audience counts (endpoint × method): '
                          + ', '.join(f'{a} {counts[a]}' for a in AUDIENCE_ORDER if counts[a]) + '\n')
        self.stdout.write('Admitted by the declared permission classes; views narrow further '
                          '(organization scoping, object checks, feature flags). "key" = organization API key.\n')
        self.stdout.write('| Route | Methods | Audience | ' + ' | '.join(ROLE_COLUMNS) + ' | Permission classes |')
        self.stdout.write('|---|---|---|' + '---|' * len(ROLE_COLUMNS) + '---|')
        for template, r in sorted(routes.items()):
            allowed = ALLOWED[r['audience']]
            cells = ['key' if r['audience'] == 'api_key' else ('✓' if c in allowed else '—') for c in ROLE_COLUMNS]
            self.stdout.write(f"| `{template}` | {' '.join(sorted(r['methods']))} | {r['audience']} | "
                              + ' | '.join(cells) + f" | {r['permissions']} |")
