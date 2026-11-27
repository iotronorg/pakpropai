"""
T-1090b / ADR-017 — request id + additive error envelope.

Covers:
  * X-Request-ID: generated, a well-formed inbound id kept, a malformed one
    replaced, echoed on success and error, put in the log context
  * DRF exceptions: precise code, existing keys unchanged, validation fields
  * hand-built {'error': '...'} responses keep their string and gain error_info
  * middleware rejections, unrouted /api/ URLs and unhandled exceptions → JSON
  * non-/api/ paths, success bodies and non-object bodies are untouched
"""
import logging
import re
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.core.context import get_request_id, set_request_id
from apps.core.errors import attach_envelope, status_code_name
from apps.core.log_filters import TraceContextFilter
from tests.factories import make_client, make_org, make_property

HEX32 = re.compile(r'^[0-9a-f]{32}$')
PROPS = '/api/v1/properties/'


def _info(resp):
    body = resp.json()
    return body, body['error_info']


class RequestIdTest(TestCase):

    def setUp(self):
        self.api = APIClient()

    def test_generated_when_absent_and_echoed_in_body(self):
        resp = self.api.get(PROPS)
        rid = resp['X-Request-ID']
        self.assertRegex(rid, HEX32)
        self.assertEqual(resp.json()['request_id'], rid)

    def test_well_formed_inbound_id_is_kept(self):
        resp = self.api.get(PROPS, HTTP_X_REQUEST_ID='lb-trace.0042_abcd')
        self.assertEqual(resp['X-Request-ID'], 'lb-trace.0042_abcd')
        self.assertEqual(resp.json()['request_id'], 'lb-trace.0042_abcd')

    def test_malformed_inbound_id_is_replaced(self):
        for bad in ('short', 'has space inside', 'x' * 65, 'evil\ninjected-log-line', '<script>alert1</script>'):
            with self.subTest(bad=bad):
                resp = self.api.get(PROPS, HTTP_X_REQUEST_ID=bad)
                self.assertRegex(resp['X-Request-ID'], HEX32)

    def test_success_response_has_header_but_body_unchanged(self):
        self.api.force_authenticate(make_client())
        resp = self.api.get(PROPS)
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp['X-Request-ID'], HEX32)
        self.assertNotIn('error_info', resp.json())
        self.assertNotIn('request_id', resp.json())

    def test_context_cleared_after_request(self):
        self.api.get(PROPS)
        self.assertEqual(get_request_id(), '')

    def test_log_records_carry_request_id(self):
        record = logging.LogRecord('x', logging.INFO, __file__, 1, 'm', (), None)
        set_request_id('abc12345')
        try:
            TraceContextFilter().filter(record)
        finally:
            set_request_id('')
        self.assertEqual(record.request_id, 'abc12345')

    def test_cors_lets_browsers_send_and_read_it(self):
        self.assertIn('X-Request-ID', settings.CORS_EXPOSE_HEADERS)
        self.assertIn('x-request-id', settings.CORS_ALLOW_HEADERS)
        self.assertIn('authorization', settings.CORS_ALLOW_HEADERS)  # defaults kept


class DrfExceptionEnvelopeTest(TestCase):

    def setUp(self):
        self.api = APIClient()

    def test_unauthenticated_keeps_detail_and_adds_code(self):
        resp = self.api.get(PROPS)
        self.assertEqual(resp.status_code, 401)
        body, info = _info(resp)
        self.assertEqual(body['detail'], 'Authentication credentials were not provided.')
        self.assertEqual(info, {
            'status': 401, 'code': 'not_authenticated',
            'message': 'Authentication credentials were not provided.',
        })

    def test_validation_error_lists_fields_and_keeps_field_keys(self):
        self.api.force_authenticate(make_client())
        resp = self.api.get(PROPS, {'min_price': 'abc'})
        self.assertEqual(resp.status_code, 400)
        body, info = _info(resp)
        self.assertEqual(body['min_price'], 'Must be a whole number.')         # unchanged
        self.assertEqual(info['code'], 'validation_error')
        self.assertEqual(info['fields'], {'min_price': 'Must be a whole number.'})
        self.assertEqual(info['message'], 'Invalid input.')

    def test_not_found_object(self):
        self.api.force_authenticate(make_client())
        resp = self.api.get(PROPS + '00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)
        body, info = _info(resp)
        self.assertIn('detail', body)
        self.assertEqual(info['code'], 'not_found')

    def test_custom_code_is_used(self):
        resp = Response({'detail': exceptions.ErrorDetail('Key bad', code='invalid_api_key')}, status=401)
        exc = exceptions.AuthenticationFailed('Key bad', code='invalid_api_key')
        self.assertEqual(attach_envelope(resp, exc).data['error_info']['code'], 'invalid_api_key')

    def test_non_object_body_is_left_alone(self):
        resp = Response(['a list'], status=400)
        attach_envelope(resp, exceptions.ValidationError(['a list']))
        self.assertEqual(resp.data, ['a list'])

    def test_existing_error_info_key_not_overwritten(self):
        resp = Response({'error_info': 'mine'}, status=400)
        attach_envelope(resp, exceptions.ValidationError({'error_info': 'mine'}))
        self.assertEqual(resp.data, {'error_info': 'mine'})


class NonExceptionErrorEnvelopeTest(TestCase):

    def test_hand_built_error_string_is_kept(self):
        api = APIClient()
        api.force_authenticate(make_client())
        prop = make_property(org=make_org(operational_mode='production'))
        resp = api.post(f'{PROPS}{prop.pk}/rescore/')
        self.assertEqual(resp.status_code, 403)
        body, info = _info(resp)
        self.assertEqual(body['error'], 'Admin only.')                         # unchanged type
        self.assertEqual(info, {'status': 403, 'code': 'listing.admin_only', 'message': 'Admin only.'})   # T-1092
        self.assertEqual(body['request_id'], resp['X-Request-ID'])

    def test_security_middleware_rejection_gets_envelope(self):
        resp = APIClient().get(PROPS, {'search': "' OR '1'='1"}, REMOTE_ADDR='10.41.0.1')
        self.assertEqual(resp.status_code, 403)
        _, info = _info(resp)
        self.assertEqual(info['status'], 403)
        self.assertTrue(info['message'])

    def test_unrouted_api_url_is_json(self):
        resp = APIClient().get('/api/v1/does-not-exist/')
        self.assertEqual(resp.status_code, 404)
        body, info = _info(resp)
        self.assertEqual(body['detail'], 'Not found.')
        self.assertEqual(info['code'], 'not_found')
        self.assertEqual(body['request_id'], resp['X-Request-ID'])

    def test_unhandled_exception_is_json_with_request_id(self):
        client = APIClient()
        client.raise_request_exception = False
        client.force_authenticate(make_client())
        with patch('apps.properties.views.PropertyViewSet.list', side_effect=RuntimeError('boom')):
            resp = client.get(PROPS, HTTP_X_REQUEST_ID='support-ticket-123')
        self.assertEqual(resp.status_code, 500)
        body, info = _info(resp)
        self.assertEqual(info['code'], 'server_error')
        self.assertNotIn('boom', resp.content.decode())                     # no internals leaked
        self.assertEqual(body['request_id'], 'support-ticket-123')
        self.assertEqual(resp['X-Request-ID'], 'support-ticket-123')

    def test_non_api_paths_are_untouched(self):
        resp = Client().get('/definitely-not-a-page/')
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn('json', resp.get('Content-Type', ''))


class ApiErrorTest(TestCase):
    """T-1092 / KI-038: refusals raised with specific codes; the legacy ``error`` string stays."""

    def test_body_keeps_error_and_gains_detail_and_code(self):
        api = APIClient()
        from tests.factories import make_agent
        api.force_authenticate(make_agent(org=make_org())[0])               # agents and admins only
        r = api.get('/api/v1/properties/compare/', {'ids': ','.join(['00000000-0000-4000-8000-000000000000'] * 5)})
        self.assertEqual(r.status_code, 400)
        body, info = _info(r)
        self.assertEqual((body['error'], body['detail']), ('Maximum 4 properties can be compared at once.',) * 2)
        self.assertEqual(info['code'], 'compare.too_many')

    def test_refusals_do_not_count_as_authentication_failures(self):
        api = APIClient()
        api.force_authenticate(make_client())
        prop = make_property(org=make_org(operational_mode='production'))
        with patch('apps.security.exception_handler._record_auth_failure') as recorded:
            self.assertEqual(api.post(f'{PROPS}{prop.pk}/rescore/', REMOTE_ADDR='203.0.113.9').status_code, 403)
            self.assertFalse(recorded.called)
            self.assertEqual(APIClient().get(PROPS, REMOTE_ADDR='203.0.113.9').status_code, 401)
            self.assertTrue(recorded.called)                    # DRF's own 401/403 still count

    def test_token_refresh_codes(self):
        r = APIClient().post('/api/v1/auth/token/refresh/')
        self.assertEqual((r.status_code, _info(r)[1]['code'], r.json()['error']),
                         (401, 'auth.refresh_missing', 'No refresh token cookie.'))
        c = APIClient()
        c.cookies['refresh_token'] = 'not-a-token'
        r = c.post('/api/v1/auth/token/refresh/')
        self.assertEqual((r.status_code, _info(r)[1]['code']), (401, 'auth.refresh_invalid'))
        self.assertEqual(r.cookies['refresh_token'].value, '')              # still cleared

    def test_no_hand_built_error_responses_remain(self):
        """New code raises ``ApiError``; a kept ``Response({'error': …})`` must name its ``code``."""
        from pathlib import Path
        pattern = re.compile(r"(?:return\s+|=\s*)Response\(\s*\{['\"]error['\"]:[^}]*\}", re.S)
        found = []
        for path in Path(settings.BASE_DIR, 'apps').rglob('*.py'):
            if 'migrations' in path.parts:
                continue
            for m in pattern.finditer(path.read_text()):
                if "'code'" not in m.group(0) and '"code"' not in m.group(0):
                    found.append(f'{path.relative_to(settings.BASE_DIR)}: {m.group(0)[:60]}')
        self.assertEqual(found, [])


class StatusCodeNameTest(SimpleTestCase):

    def test_mapping(self):
        self.assertEqual(status_code_name(429), 'throttled')
        self.assertEqual(status_code_name(418), 'error')
        self.assertEqual(status_code_name(599), 'server_error')
