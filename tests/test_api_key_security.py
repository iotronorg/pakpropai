"""
Security test suite for the DeveloperApiKey system.

Covers:
  1. ApiKeyManager — generation, verification, expiry, revocation
  2. ApiKeyAuthentication — DRF auth class behavior
  3. HasApiKeyScope — scope enforcement
  4. Cross-org isolation — Org A key cannot access Org B's data (asserts 403 + security log)
  5. Key management endpoints — create, list, revoke (JWT auth)
"""
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, RequestFactory
from django.utils import timezone

from apps.organizations.api_keys import ApiKeyManager
from apps.organizations.models import DeveloperApiKey, Organization
from apps.organizations.external_api import (
    ApiKeyAuthentication,
    HasApiKeyScope,
    ExternalLeadDetailView,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_org(name='Test Org') -> Organization:
    return Organization.objects.create(name=name)


def _make_key(org, scopes=None) -> tuple[str, DeveloperApiKey]:
    return ApiKeyManager.generate(
        organization=org,
        name='Test Key',
        scopes=scopes or ['leads:read', 'inventory:read'],
    )


# ── 1. ApiKeyManager ───────────────────────────────────────────────────────────

class TestApiKeyManagerGeneration(TestCase):

    def test_generate_returns_raw_token_and_instance(self):
        org = _make_org()
        raw, instance = _make_key(org)

        self.assertIsInstance(raw, str)
        self.assertTrue(raw.startswith('rtk_'))
        # split with maxsplit=2: 'rtk' / '<prefix12>' / '<secret48>'
        # (secret48 may contain '_' chars from base64url encoding)
        parts = raw.split('_', 2)
        self.assertEqual(len(parts), 3)
        self.assertIsInstance(instance, DeveloperApiKey)
        self.assertEqual(instance.organization, org)
        self.assertTrue(instance.is_active)

    def test_raw_token_not_stored(self):
        org = _make_org()
        raw, instance = _make_key(org)

        # The full secret must NOT appear anywhere in DB-persisted fields
        prefix = raw.split('_')[1]
        secret = raw.split('_')[2]
        self.assertEqual(instance.key_prefix, prefix)
        self.assertNotIn(secret, instance.key_hash)
        self.assertNotIn(secret, instance.key_salt)

    def test_token_format_is_rtk_prefix_secret(self):
        org = _make_org()
        raw, instance = _make_key(org)
        parts = raw.split('_', 2)  # maxsplit=2 — secret may contain '_'

        self.assertEqual(parts[0], 'rtk')
        self.assertEqual(len(parts[1]), 12)        # prefix12
        self.assertGreaterEqual(len(parts[2]), 40) # secret48

    def test_invalid_scopes_raise_value_error(self):
        org = _make_org()
        with self.assertRaises(ValueError) as ctx:
            ApiKeyManager.generate(org, 'Bad Key', scopes=['not_a_scope'])
        self.assertIn('Invalid scopes', str(ctx.exception))

    def test_verify_correct_token(self):
        org = _make_org()
        raw, instance = _make_key(org)
        self.assertTrue(ApiKeyManager.verify(raw, instance))

    def test_verify_tampered_secret_returns_false(self):
        org = _make_org()
        raw, instance = _make_key(org)
        parts = raw.split('_')
        tampered = f"rtk_{parts[1]}_{'X' * len(parts[2])}"
        self.assertFalse(ApiKeyManager.verify(tampered, instance))

    def test_verify_wrong_prefix_returns_false(self):
        org = _make_org()
        raw, instance = _make_key(org)
        parts = raw.split('_')
        tampered = f"rtk_{'a' * 12}_{parts[2]}"
        self.assertFalse(ApiKeyManager.verify(tampered, instance))

    def test_authenticate_valid_token(self):
        org = _make_org()
        raw, instance = _make_key(org)

        with patch.object(ApiKeyManager, '_touch_last_used'):
            result = ApiKeyManager.authenticate(raw)

        self.assertIsNotNone(result)
        self.assertEqual(result.pk, instance.pk)

    def test_authenticate_expired_token_returns_none(self):
        org = _make_org()
        raw, instance = _make_key(org)
        instance.expires_at = timezone.now() - timedelta(hours=1)
        instance.save(update_fields=['expires_at'])

        with patch.object(ApiKeyManager, '_touch_last_used'):
            result = ApiKeyManager.authenticate(raw)
        self.assertIsNone(result)

    def test_authenticate_revoked_token_returns_none(self):
        org = _make_org()
        raw, instance = _make_key(org)
        instance.is_active = False
        instance.save(update_fields=['is_active'])

        with patch.object(ApiKeyManager, '_touch_last_used'):
            result = ApiKeyManager.authenticate(raw)
        self.assertIsNone(result)

    def test_authenticate_garbage_returns_none(self):
        self.assertIsNone(ApiKeyManager.authenticate('not_a_token'))
        self.assertIsNone(ApiKeyManager.authenticate(''))
        self.assertIsNone(ApiKeyManager.authenticate('Bearer abc123'))

    def test_each_key_has_unique_prefix(self):
        org = _make_org()
        _, k1 = _make_key(org)
        _, k2 = _make_key(org)
        self.assertNotEqual(k1.key_prefix, k2.key_prefix)

    def test_each_key_has_unique_salt(self):
        org = _make_org()
        _, k1 = _make_key(org)
        _, k2 = _make_key(org)
        self.assertNotEqual(k1.key_salt, k2.key_salt)


# ── 2. ApiKeyAuthentication ────────────────────────────────────────────────────

class TestApiKeyAuthentication(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.auth    = ApiKeyAuthentication()

    def _request_with_bearer(self, token: str):
        req = MagicMock()
        req.META = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        return req

    def _request_with_header(self, token: str):
        req = MagicMock()
        req.META = {'HTTP_X_API_KEY': token, 'HTTP_AUTHORIZATION': ''}
        return req

    def test_returns_none_when_no_rtk_header(self):
        req = MagicMock()
        req.META = {'HTTP_AUTHORIZATION': 'Bearer some_jwt_token'}
        self.assertIsNone(self.auth.authenticate(req))

    def test_raises_on_invalid_token(self):
        from rest_framework.exceptions import AuthenticationFailed
        req = self._request_with_bearer('rtk_invalid_garbage_token_12345')
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(req)

    def test_authenticates_via_bearer_header(self):
        from rest_framework.exceptions import AuthenticationFailed
        org = _make_org('Auth Bearer Org')
        raw, instance = _make_key(org)

        with patch.object(ApiKeyManager, '_touch_last_used'):
            req = self._request_with_bearer(raw)
            user, key = self.auth.authenticate(req)

        self.assertEqual(key.pk, instance.pk)

    def test_authenticates_via_x_api_key_header(self):
        org = _make_org('Auth Header Org')
        raw, instance = _make_key(org)

        with patch.object(ApiKeyManager, '_touch_last_used'):
            req = self._request_with_header(raw)
            user, key = self.auth.authenticate(req)

        self.assertEqual(key.pk, instance.pk)


# ── 3. HasApiKeyScope ──────────────────────────────────────────────────────────

class TestHasApiKeyScopePermission(TestCase):

    def _request_with_key(self, api_key):
        req = MagicMock()
        req.auth = api_key
        req.method = 'GET'
        req.path = '/api/v1/external/leads/'
        return req

    def test_allows_when_scope_present(self):
        org = _make_org('Scope Org')
        _, key = _make_key(org, scopes=['leads:read'])

        view = MagicMock()
        view.required_scope = 'leads:read'
        req = self._request_with_key(key)

        perm = HasApiKeyScope()
        self.assertTrue(perm.has_permission(req, view))

    def test_denies_when_scope_missing(self):
        org = _make_org('Scope Deny Org')
        _, key = _make_key(org, scopes=['inventory:read'])

        view = MagicMock()
        view.required_scope = 'leads:read'
        req = self._request_with_key(key)

        perm = HasApiKeyScope()
        self.assertFalse(perm.has_permission(req, view))

    def test_denies_non_api_key_auth(self):
        view = MagicMock()
        view.required_scope = 'leads:read'
        req = MagicMock()
        req.auth = 'some_jwt_token'

        perm = HasApiKeyScope()
        self.assertFalse(perm.has_permission(req, view))


# ── 4. Cross-org isolation ─────────────────────────────────────────────────────

class TestCrossOrgIsolation(TestCase):
    """
    Core security invariant: an API key belonging to Org A must never be able
    to read or modify data belonging to Org B.

    Assertion: HTTP 403 is returned and a CROSS_ORG_ACCESS_ATTEMPT warning is
    emitted to the 'security.api_keys' logger.
    """

    def setUp(self):
        from apps.leads.models import Lead
        from apps.users.models import User
        from rest_framework.test import APIClient

        self.org_a = Organization.objects.create(name='Org Alpha')
        self.org_b = Organization.objects.create(name='Org Beta')

        self.raw_a, self.key_a = _make_key(self.org_a, scopes=['leads:read'])

        self.user_b = User.objects.create_user(phone='+923001111111')
        self.user_a = User.objects.create_user(phone='+923002222222')

        self.lead_b = Lead.objects.create(organization=self.org_b, user=self.user_b)

        # APIClient that presents Org A's raw token via X-API-Key header
        self.client_a = APIClient()
        self.client_a.credentials(HTTP_X_API_KEY=self.raw_a)

    def _mock_request(self):
        """Bare request with request.auth pre-set — for direct permission tests only."""
        req = MagicMock()
        req.auth = self.key_a
        req.method = 'GET'
        req.path = '/api/v1/external/leads/'
        return req

    def test_cross_org_object_permission_returns_false(self):
        """has_object_permission rejects objects from a different org."""
        perm = HasApiKeyScope()
        req = self._mock_request()
        allowed = perm.has_object_permission(req, MagicMock(), self.lead_b)
        self.assertFalse(allowed)

    def test_cross_org_access_attempt_is_logged(self):
        """A cross-org attempt must emit a WARNING to the security logger."""
        perm = HasApiKeyScope()
        req = self._mock_request()

        with self.assertLogs('security.api_keys', level='WARNING') as log:
            perm.has_object_permission(req, MagicMock(), self.lead_b)

        self.assertTrue(
            any('CROSS_ORG_ACCESS_ATTEMPT' in line for line in log.output),
            f"Expected CROSS_ORG_ACCESS_ATTEMPT in logs, got: {log.output}",
        )

    def test_external_lead_detail_returns_403_for_cross_org(self):
        """
        GET /api/v1/external/leads/<lead_b_id>/ with Org A's key → HTTP 403.
        This is the end-to-end isolation assertion.
        """
        with patch.object(ApiKeyManager, '_touch_last_used'), \
             self.assertLogs('security.api_keys', level='WARNING') as log:
            response = self.client_a.get(
                f'/api/v1/external/leads/{self.lead_b.pk}/'
            )

        self.assertEqual(
            response.status_code, 403,
            f"Expected 403, got {response.status_code}. "
            "Org A key must not access Org B lead.",
        )
        self.assertTrue(
            any('CROSS_ORG_ACCESS_ATTEMPT' in line for line in log.output),
        )

    def test_own_org_lead_is_accessible(self):
        """A lead from the same org must return 200."""
        from apps.leads.models import Lead

        lead_a = Lead.objects.create(organization=self.org_a, user=self.user_a)

        with patch.object(ApiKeyManager, '_touch_last_used'):
            response = self.client_a.get(
                f'/api/v1/external/leads/{lead_a.pk}/'
            )

        self.assertEqual(response.status_code, 200)

    def test_same_prefix_different_org_cannot_verify(self):
        """
        A key with a valid prefix but the wrong secret must not authenticate.
        Simulates a collision/forgery attempt.
        """
        raw_b, key_b = _make_key(self.org_b, scopes=['leads:read'])

        parts = raw_b.split('_', 2)
        forged = f"rtk_{parts[1]}_{'X' * len(parts[2])}"

        result = ApiKeyManager.authenticate(forged)
        self.assertIsNone(result)


# ── 5. Key management endpoints ────────────────────────────────────────────────

class TestKeyManagementEndpoints(TestCase):

    def setUp(self):
        from apps.users.models import User
        from rest_framework.test import APIClient

        self.org = Organization.objects.create(name='Dev Org')
        self.user = User.objects.create_user(phone='+923009990001')
        self.user.role = 'developer'
        self.user.save(update_fields=['role'])
        self.org.admin_user = self.user
        self.org.save(update_fields=['admin_user'])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_key_returns_201_with_token(self):
        response = self.client.post(
            '/api/v1/external/keys/',
            {'name': 'HubSpot Integration', 'scopes': ['leads:read', 'inventory:read']},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.data
        self.assertIn('token', data)
        self.assertTrue(data['token'].startswith('rtk_'))
        self.assertIn('warning', data)

    def test_list_keys_returns_200(self):
        _make_key(self.org)
        response = self.client.get('/api/v1/external/keys/')
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertGreaterEqual(len(response.data), 1)
        for key_data in response.data:
            # Raw secret must never appear in list response
            self.assertNotIn('token', key_data)
            self.assertIn('****', key_data['key_prefix'])

    def test_revoke_key_sets_is_active_false(self):
        _, key = _make_key(self.org)
        response = self.client.delete(f'/api/v1/external/keys/{key.pk}/')
        self.assertEqual(response.status_code, 204)
        key.refresh_from_db()
        self.assertFalse(key.is_active)

    def test_revoked_key_cannot_authenticate(self):
        _, key = _make_key(self.org)
        key.is_active = False
        key.save(update_fields=['is_active'])

        # Even with a structurally valid token, auth must fail
        result = ApiKeyManager.authenticate(f"rtk_{key.key_prefix}_{'X' * 48}")
        self.assertIsNone(result)

    def test_unauthenticated_request_returns_401(self):
        from rest_framework.test import APIClient
        anon = APIClient()
        response = anon.get('/api/v1/external/keys/')
        self.assertEqual(response.status_code, 401)
