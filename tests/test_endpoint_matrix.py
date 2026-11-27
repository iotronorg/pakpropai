"""T-6030 — role × endpoint permission matrix (MVP_SCOPE §6 criteria 10 and 12).

Every DRF endpoint under /api/ (including views hidden from the schema) is called:

* anonymously: it must refuse (401/403) unless it is in ``PUBLIC`` with a reason;
* as a consumer, an org agent and an org owner, according to the audience its declared permission
  classes give it (``apps.security.permission_matrix.audience``).

Refusals happen in DRF's permission check, before the handler runs, so these calls change nothing.
Outbound network is blocked for the whole test so an endpoint that does run cannot reach a provider.
"""
from contextlib import ExitStack
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.security.permission_matrix import endpoints, unresolved
from tests.factories import make_agent, make_client, make_developer, make_user

DENIED = {401, 403}

_LOGIN = 'sign-in / sign-up flow: rate-limited, OTP or password checked in the view'
_WEBHOOK = 'provider webhook: authenticity checked in the view (signature or read-back), fails closed'
_PUBLIC_READ = 'public, read-only consumer surface (published, non-sandbox listings), rate-limited'

# (template, method) → why it answers without authentication. Reviewed in T-6030; keep it short.
PUBLIC: dict[tuple[str, str], str] = {
    ('/api/v1/agents/register/', 'POST'): _LOGIN,
    ('/api/v1/auth/csrf/', 'GET'): 'issues the CSRF cookie the login form needs',
    ('/api/v1/auth/login/', 'POST'): _LOGIN,
    ('/api/v1/auth/mfa/verify/', 'POST'): _LOGIN + ' (needs the short-lived MFA challenge)',
    ('/api/v1/auth/otp/send/', 'POST'): _LOGIN,
    ('/api/v1/auth/otp/verify/', 'POST'): _LOGIN,
    ('/api/v1/auth/password/reset/confirm/', 'POST'): _LOGIN + ' (needs the reset token)',
    ('/api/v1/auth/password/reset/request/', 'POST'): _LOGIN,
    ('/api/v1/auth/registration/verify-otp/', 'POST'): _LOGIN,
    ('/api/v1/organizations/register/', 'POST'): _LOGIN,
    ('/api/v1/organizations/register/verify-otp/', 'POST'): _LOGIN,
    ('/api/v1/billing/webhook/bsecure/', 'POST'): _WEBHOOK,
    ('/api/v1/billing/webhook/safepay/', 'POST'): _WEBHOOK,
    ('/api/v1/billing/webhook/stripe/', 'POST'): _WEBHOOK,
    ('/api/v1/payments/webhook/bsecure/', 'POST'): _WEBHOOK + ' (answers 200 activated=false)',
    ('/api/v1/payments/webhook/bsecure/deal-lock/', 'POST'): _WEBHOOK,
    ('/api/v1/payments/webhook/safepay/', 'POST'): _WEBHOOK + ' (answers 200 activated=false)',
    ('/api/v1/payments/webhook/safepay/deal-lock/', 'POST'): _WEBHOOK,
    ('/api/v1/payments/webhook/stripe/deal-lock/', 'POST'): _WEBHOOK,
    ('/api/v1/verification/webhook/jumio/', 'POST'): _WEBHOOK + ' (unsigned by Jumio: the result is read back, KI-097)',
    ('/api/v1/verification/webhook/onfido/', 'POST'): _WEBHOOK,
    ('/api/v1/verification/webhook/stripe-identity/', 'POST'): _WEBHOOK,
    ('/api/v1/investment/calculate/', 'POST'): 'calculator, no model call and no stored data (T-4010)',
    ('/api/v1/price-context/', 'GET'): _PUBLIC_READ + ' (aggregates only, T-4020)',
    ('/api/v1/public/listings/compare/', 'GET'): _PUBLIC_READ,
    ('/api/v1/public/listings/search/', 'GET'): _PUBLIC_READ,
    ('/api/v1/public/listings/{slug_or_id}/', 'GET'): _PUBLIC_READ,
    ('/api/v1/search/', 'POST'): _PUBLIC_READ + ' (search v2, T-2030)',
    ('/api/v1/saved-search-stop/', 'POST'): 'one-click unsubscribe from an alert email: needs the signed stop token',
    ('/api/v1/theme/', 'GET'): 'white-label colours and logo for the login page, no private data',
}

# audience → the test users that must be refused.
MUST_REFUSE = {
    'staff': ('consumer', 'agent', 'owner'),
    'moderator': ('consumer', 'agent', 'owner'),
    'org_admin': ('consumer', 'agent'),
    'dashboard': ('consumer',),
    'api_key': ('consumer', 'agent', 'owner'),
}


def _no_network():
    stack = ExitStack()
    boom = mock.Mock(side_effect=OSError('network blocked in the permission matrix test'))
    stack.enter_context(mock.patch('requests.Session.request', boom))
    stack.enter_context(mock.patch('urllib.request.urlopen', boom))
    return stack


def call(client, ep):
    fn = getattr(client, ep.method.lower())
    kwargs = {'format': 'json'} if ep.method in ('POST', 'PUT', 'PATCH') else {}
    try:
        return fn(ep.path, {} if kwargs else None, **kwargs).status_code
    except Exception as exc:          # a handler that crashed was not refused
        return f'error:{exc.__class__.__name__}'


class PermissionMatrixTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.eps = endpoints()
        cls.owner, cls.org = make_developer(org_name='Matrix Org')
        cls.users = {
            'consumer': make_client(),
            'agent': make_agent(org=cls.org)[0],
            'owner': cls.owner,
            'admin': make_user(role='admin', platform_role='super_admin'),
        }

    def client_for(self, who):
        client = APIClient()
        client.force_authenticate(self.users[who])
        return client

    def setUp(self):
        stack = _no_network()
        stack.__enter__()
        self.addCleanup(stack.__exit__, None, None, None)

    def test_every_endpoint_is_covered(self):
        self.assertGreater(len(self.eps), 300)
        self.assertEqual(unresolved(), [])

    def test_anonymous(self):
        client = APIClient()
        open_ = [f'{ep.method} {ep.template} → {s} [{ep.permissions}]'
                 for ep in self.eps if (ep.template, ep.method) not in PUBLIC
                 for s in [call(client, ep)] if s not in DENIED]
        self.assertEqual(open_, [], '\n'.join(open_))

    def test_public_list_has_no_stale_entries(self):
        live = {(ep.template, ep.method) for ep in self.eps}
        self.assertEqual(sorted(set(PUBLIC) - live), [])

    def test_roles_are_refused_outside_their_audience(self):
        clients = {who: self.client_for(who) for who in ('consumer', 'agent', 'owner')}
        let_in = []
        for ep in self.eps:
            for who in MUST_REFUSE.get(ep.audience, ()):
                status = call(clients[who], ep)
                if status not in DENIED:
                    let_in.append(f'{who}: {ep.method} {ep.template} → {status} [{ep.audience}: {ep.permissions}]')
        self.assertEqual(let_in, [], '\n'.join(let_in))

    def test_the_right_role_gets_past_the_permission_check(self):
        # Controls for the test above: without them a broken test user would make every refusal pass.
        who_for = {'staff': 'admin', 'moderator': 'admin', 'org_admin': 'owner', 'dashboard': 'agent'}
        clients = {who: self.client_for(who) for who in set(who_for.values())}
        refused = []
        for ep in self.eps:
            if ep.method != 'GET' or ep.audience not in who_for:
                continue
            status = call(clients[who_for[ep.audience]], ep)
            if status in DENIED:
                refused.append(f'{who_for[ep.audience]}: GET {ep.template} → {status} [{ep.permissions}]')
        self.assertEqual(refused, [], '\n'.join(refused))


class MatrixCommandTest(TestCase):
    def test_markdown_and_json(self):
        import json
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('permission_matrix', stdout=out)
        text = out.getvalue()
        self.assertIn('| `/api/v1/auth/login/` | POST | public | ✓ |', text)
        self.assertRegex(text, r'\| `/api/v1/ai/kill-switches/` \| GET POST \| staff \| — \| — \| — \| — \| ✓ \|')
        out = StringIO()
        call_command('permission_matrix', '--json', '--audience', 'public', stdout=out)
        routes = json.loads(out.getvalue())
        self.assertTrue(set(routes) >= {t for t, _ in PUBLIC})
        self.assertEqual({r['audience'] for r in routes.values()}, {'public'})
