"""
T-1033 (KI-032) — PII masking per-endpoint field allow-list: listed top-level
fields keep raw contact data; other fields in the same body (free text) and
every non-listed endpoint stay masked.
"""
import json
from unittest.mock import MagicMock

from django.test import RequestFactory, TestCase, override_settings

from apps.compliance.middleware import PIIMaskingMiddleware

PHONE = '+923001234567'
EMAIL = 'agent@example.com'


def _run(path, body, method='post'):
    req = getattr(RequestFactory(), method)(path, data=json.dumps(body).encode(),
                                            content_type='application/json')
    PIIMaskingMiddleware(get_response=lambda r: MagicMock(status_code=200))(req)
    return json.loads(req.body)


class AllowlistTest(TestCase):
    def test_agent_register_keeps_contact_fields_masks_free_text(self):
        out = _run('/api/v1/agents/register/',
                   {'phone': PHONE, 'email': EMAIL, 'name': 'A',
                    'bio': f'call me on {PHONE}', 'areas': [f'mail {EMAIL}']})
        self.assertEqual((out['phone'], out['email']), (PHONE, EMAIL))
        self.assertNotIn(PHONE, out['bio'])
        self.assertNotIn(EMAIL, out['areas'][0])

    def test_member_invite_phone_only(self):
        out = _run('/api/v1/organizations/me/members/', {'phone': PHONE, 'note': PHONE})
        self.assertEqual(out['phone'], PHONE)
        self.assertNotIn(PHONE, out['note'])

    def test_method_and_path_must_match(self):
        # PATCH on members detail is not allow-listed.
        out = _run('/api/v1/organizations/me/members/5/', {'phone': PHONE}, method='patch')
        self.assertNotEqual(out['phone'], PHONE)
        # agents/me allows PATCH, not POST.
        out = _run('/api/v1/agents/me/', {'phone': PHONE}, method='post')
        self.assertNotEqual(out['phone'], PHONE)
        out = _run('/api/v1/agents/me/', {'whatsapp_number': PHONE}, method='patch')
        self.assertEqual(out['whatsapp_number'], PHONE)
        out = _run('/api/v1/agents/12/', {'email': EMAIL}, method='patch')
        self.assertEqual(out['email'], EMAIL)

    def test_org_contact_fields(self):
        out = _run('/api/v1/organizations/me/', {'phone': PHONE, 'description': PHONE}, method='patch')
        self.assertEqual(out['phone'], PHONE)
        self.assertNotIn(PHONE, out['description'])

    def test_non_listed_endpoint_and_nested_keys_masked(self):
        out = _run('/api/v1/leads/', {'phone': PHONE, 'notes': PHONE})
        self.assertNotIn(PHONE, json.dumps(out))
        out = _run('/api/v1/agents/register/', {'profile': {'phone': PHONE}})
        self.assertNotIn(PHONE, json.dumps(out))       # only top-level keys are allowed

    def test_former_path_exemptions_removed(self):
        from django.conf import settings
        self.assertNotIn('/api/v1/agents/register/', settings.PRIVACY_MASK_EXEMPT_PATHS)
        self.assertNotIn('/api/v1/organizations/me/members/', settings.PRIVACY_MASK_EXEMPT_PATHS)

    @override_settings(PRIVACY_MASK_FIELD_ALLOWLIST=[])
    def test_empty_allowlist_masks_everything(self):
        out = _run('/api/v1/agents/register/', {'phone': PHONE})
        self.assertNotEqual(out['phone'], PHONE)
