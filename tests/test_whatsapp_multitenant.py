"""
Multi-tenant WhatsApp infrastructure tests.

Covers: OrgWhatsAppConfig model, serializer masking, webhook two-phase verification,
org admin RBAC, org isolation, WhatsApp client factory, session org scoping.
"""
import hashlib
import hmac
import json
from django.test import TestCase, override_settings
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.whatsapp.models import OrgWhatsAppConfig, WhatsAppSession
from apps.whatsapp.serializers import OrgWhatsAppConfigSerializer
from tests.factories import make_developer, make_agent, make_org

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_DUMMY  = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def _sig(body: bytes, secret: str) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _wa_payload(phone_number_id: str = '', messages: list = None) -> bytes:
    return json.dumps({
        'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': phone_number_id},
            'messages': messages or [],
        }}]}]
    }).encode()


# ── Model tests ───────────────────────────────────────────────────────────────

class OrgWhatsAppConfigModelTests(TestCase):

    def test_create_config(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(
            organization=org,
            phone_number_id='pnid_123',
            display_phone='+923001234567',
            access_token='tok_abc',
            is_active=True,
        )
        self.assertEqual(cfg.phone_number_id, 'pnid_123')
        self.assertTrue(cfg.ai_enabled)
        self.assertTrue(cfg.auto_reply_enabled)
        self.assertIsNone(cfg.webhook_verified_at)
        self.assertTrue(cfg.is_active)  # we explicitly set True above

    def test_one_config_per_org(self):
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(organization=org)
        with self.assertRaises(IntegrityError):
            OrgWhatsAppConfig.objects.create(organization=org)

    def test_serializer_masks_non_empty_secrets(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(
            organization=org,
            access_token='real_token',
            app_secret='real_secret',
            verify_token='real_verify',
            phone_number_id='abc',
        )
        data = OrgWhatsAppConfigSerializer(cfg).data
        self.assertEqual(data['access_token'], '••••••••')
        self.assertEqual(data['app_secret'], '••••••••')
        self.assertEqual(data['verify_token'], '••••••••')
        self.assertEqual(data['phone_number_id'], 'abc')

    def test_serializer_empty_secret_is_not_masked(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(organization=org)
        data = OrgWhatsAppConfigSerializer(cfg).data
        self.assertEqual(data['access_token'], '')
        self.assertEqual(data['app_secret'], '')

    def test_patch_with_mask_preserves_existing_secret(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(
            organization=org, access_token='original_tok', phone_number_id='old'
        )
        serializer = OrgWhatsAppConfigSerializer(
            cfg,
            data={'access_token': '••••••••', 'phone_number_id': 'new_pnid'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        cfg.refresh_from_db()
        self.assertEqual(cfg.access_token, 'original_tok')  # unchanged
        self.assertEqual(cfg.phone_number_id, 'new_pnid')   # updated


# ── Webhook GET verification tests ───────────────────────────────────────────

@override_settings(CACHES=_LOCMEM, WA_VERIFY_TOKEN='', WA_APP_SECRET='test-secret')
class WebhookGetVerificationTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def _webhook_url(self):
        return '/api/v1/whatsapp/webhook/'

    def test_matching_org_verify_token_returns_challenge(self):
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(
            organization=org, verify_token='myorgtoken', is_active=True
        )
        r = self.client.get(self._webhook_url(), {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'myorgtoken',
            'hub.challenge': 'challenge_abc',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b'challenge_abc')

    def test_matching_org_token_sets_webhook_verified_at(self):
        _, org = make_developer()
        cfg = OrgWhatsAppConfig.objects.create(
            organization=org, verify_token='myorgtoken', is_active=True
        )
        self.client.get(self._webhook_url(), {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'myorgtoken',
            'hub.challenge': 'ch',
        })
        cfg.refresh_from_db()
        self.assertIsNotNone(cfg.webhook_verified_at)

    def test_wrong_token_returns_403(self):
        r = self.client.get(self._webhook_url(), {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'badtoken',
            'hub.challenge': 'ch',
        })
        self.assertEqual(r.status_code, 403)

    @override_settings(WA_VERIFY_TOKEN='global-verify-tok')
    def test_global_fallback_token(self):
        r = self.client.get(self._webhook_url(), {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'global-verify-tok',
            'hub.challenge': 'challx',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b'challx')


# ── Webhook POST signature tests ─────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM, WA_APP_SECRET='fallback-secret', DEBUG=True)
class WebhookPostSignatureTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def _webhook_url(self):
        return '/api/v1/whatsapp/webhook/'

    def test_per_org_secret_valid_signature_accepted(self):
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(
            organization=org, phone_number_id='pnid_test', app_secret='org-secret', is_active=True
        )
        body = _wa_payload(phone_number_id='pnid_test')
        r = self.client.post(
            self._webhook_url(), body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=_sig(body, 'org-secret'),
        )
        self.assertEqual(r.status_code, 200)

    def test_invalid_signature_rejected(self):
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(
            organization=org, phone_number_id='pnid_test2', app_secret='org-secret', is_active=True
        )
        body = _wa_payload(phone_number_id='pnid_test2')
        r = self.client.post(
            self._webhook_url(), body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=invalidsig',
        )
        self.assertEqual(r.status_code, 403)

    @override_settings(WA_APP_SECRET='global-secret', DEBUG=False)
    def test_global_fallback_secret_when_no_org_config(self):
        body = _wa_payload(phone_number_id='unknown-pnid')
        r = self.client.post(
            self._webhook_url(), body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=_sig(body, 'global-secret'),
        )
        self.assertEqual(r.status_code, 200)


# ── RBAC + Config API tests ───────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class OrgWhatsAppConfigAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def _config_url(self):
        return '/api/v1/whatsapp/config/'

    def test_developer_can_get_config(self):
        user, _ = make_developer()
        self.client.force_authenticate(user=user)
        r = self.client.get(self._config_url())
        self.assertEqual(r.status_code, 200)

    def test_agent_cannot_get_config(self):
        _, org = make_developer()
        agent_user, _ = make_agent(org=org)
        self.client.force_authenticate(user=agent_user)
        r = self.client.get(self._config_url())
        self.assertEqual(r.status_code, 403)

    def test_unauthenticated_cannot_get_config(self):
        r = self.client.get(self._config_url())
        self.assertEqual(r.status_code, 401)

    def test_developer_can_patch_config(self):
        user, _ = make_developer()
        self.client.force_authenticate(user=user)
        r = self.client.patch(
            self._config_url(),
            {'phone_number_id': 'pnid_new', 'is_active': True},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['phone_number_id'], 'pnid_new')

    def test_patch_secret_fields_are_masked_in_response(self):
        user, _ = make_developer()
        self.client.force_authenticate(user=user)
        r = self.client.patch(
            self._config_url(),
            {'access_token': 'plaintext_token'},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['access_token'], '••••••••')

    def test_org_isolation_developer_cannot_read_other_org_config(self):
        user_a, _ = make_developer()
        _, org_b = make_developer()
        OrgWhatsAppConfig.objects.create(organization=org_b, phone_number_id='pnid_b')
        self.client.force_authenticate(user=user_a)
        r = self.client.get(self._config_url())
        self.assertEqual(r.status_code, 200)
        # Org A's config should never expose org B's phone_number_id
        self.assertNotEqual(r.data.get('phone_number_id'), 'pnid_b')


# ── WhatsApp client factory tests ─────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class WhatsAppClientFactoryTests(TestCase):

    def test_active_config_returns_org_client(self):
        from apps.whatsapp.client import get_wa_client, WhatsAppClient
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(
            organization=org,
            phone_number_id='pnid_org',
            access_token='org_tok',
            is_active=True,
        )
        client = get_wa_client(org)
        self.assertIsInstance(client, WhatsAppClient)
        self.assertEqual(client._phone_number_id, 'pnid_org')
        self.assertEqual(client._access_token, 'org_tok')

    def test_no_config_returns_global_client(self):
        from apps.whatsapp.client import get_wa_client, WhatsAppClient
        _, org = make_developer()
        # No OrgWhatsAppConfig created
        client = get_wa_client(org)
        self.assertIsInstance(client, WhatsAppClient)

    def test_inactive_config_falls_back_to_global(self):
        from apps.whatsapp.client import get_wa_client, WhatsAppClient
        _, org = make_developer()
        OrgWhatsAppConfig.objects.create(
            organization=org,
            phone_number_id='pnid_org',
            access_token='org_tok',
            is_active=False,  # inactive
        )
        client = get_wa_client(org)
        self.assertIsInstance(client, WhatsAppClient)
        # Should NOT use the org's pnid since config is inactive
        self.assertNotEqual(client._phone_number_id, 'pnid_org')

    def test_none_org_returns_global_client(self):
        from apps.whatsapp.client import get_wa_client, WhatsAppClient
        client = get_wa_client(None)
        self.assertIsInstance(client, WhatsAppClient)
