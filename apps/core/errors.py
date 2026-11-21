"""
Consistent API error envelope (T-1090b, ADR-017).

Every JSON error response under ``/api/`` gains two keys **in addition to**
whatever the endpoint already returned. Existing keys (``detail``, field-keyed
messages, the string ``error`` used by hand-built responses) are never changed,
so current clients keep working::

    {
      "detail": "Authentication credentials were not provided.",   # unchanged
      "error_info": {
        "status": 401,
        "code": "not_authenticated",          # stable, machine-readable
        "message": "Authentication credentials were not provided.",
        "fields": {"phone": ["This field is required."]}   # validation only
      },
      "request_id": "3f2a…"                    # = X-Request-ID header
    }

Two layers fill it:
  * ``attach_envelope`` (DRF exception handler) knows the exception, so it
    gives precise codes (``not_authenticated``, ``throttled``,
    ``validation_error`` + ``fields``).
  * ``ErrorEnvelopeMiddleware`` covers everything else — hand-built
    ``Response({'error': ...}, status=4xx)``, middleware rejections, the 404/500
    handlers — with a status-derived code.

Bodies that are not JSON objects are left untouched; the request id is still
in the ``X-Request-ID`` header.
"""
import json

from django.http import JsonResponse
from django.views import defaults as django_defaults
from rest_framework import exceptions

from apps.core.context import get_request_id

ENVELOPE_KEY = 'error_info'
NON_FIELD_KEYS = ('non_field_errors', 'detail')
MAX_REWRITE_BYTES = 256 * 1024   # never re-parse large bodies

STATUS_CODES = {
    400: 'bad_request', 401: 'not_authenticated', 402: 'payment_required',
    403: 'permission_denied', 404: 'not_found', 405: 'method_not_allowed',
    406: 'not_acceptable', 409: 'conflict', 410: 'gone', 413: 'payload_too_large',
    415: 'unsupported_media_type', 422: 'unprocessable', 423: 'locked',
    429: 'throttled', 500: 'server_error', 502: 'bad_gateway',
    503: 'service_unavailable', 504: 'gateway_timeout',
}


def _first_message(value) -> str:
    """First human-readable string inside a DRF-style detail structure."""
    if isinstance(value, dict):
        for item in value.values():
            if found := _first_message(item):
                return found
        return ''
    if isinstance(value, (list, tuple)):
        for item in value:
            if found := _first_message(item):
                return found
        return ''
    return str(value) if value is not None else ''


def status_code_name(status_code: int) -> str:
    if status_code in STATUS_CODES:
        return STATUS_CODES[status_code]
    return 'server_error' if status_code >= 500 else 'error'


def build_error(status_code: int, code: str, message: str, fields=None) -> dict:
    error = {'status': status_code, 'code': code, 'message': message}
    if fields:
        error['fields'] = fields
    return {ENVELOPE_KEY: error, 'request_id': get_request_id()}


def _has_envelope(data: dict) -> bool:
    return ENVELOPE_KEY in data or 'request_id' in data


# ── Layer 1: DRF exceptions ──────────────────────────────────────────────────

def _code_for(exc, status_code: int) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return 'validation_error'
    if isinstance(exc, exceptions.APIException):
        codes = exc.get_codes()
        if isinstance(codes, str):
            return codes
        if isinstance(codes, dict) and isinstance(codes.get('detail'), str):
            return codes['detail']
        return exc.default_code
    return status_code_name(status_code)


def attach_envelope(response, exc):
    """Add the envelope to a DRF error response (in place)."""
    data = response.data
    if not isinstance(data, dict) or _has_envelope(data):
        return response

    fields = None
    if isinstance(exc, exceptions.ValidationError):
        fields = {k: v for k, v in data.items() if k not in NON_FIELD_KEYS} or None
        message = _first_message(data.get('non_field_errors') or data.get('detail')) or 'Invalid input.'
    else:
        message = _first_message(data.get('detail', data)) or 'Error.'

    data.update(build_error(response.status_code, _code_for(exc, response.status_code), message, fields))
    return response


# ── Layer 2: any other JSON error response ───────────────────────────────────

def _generic_envelope(data: dict, status_code: int) -> dict:
    code = data.get('code') if isinstance(data.get('code'), str) else status_code_name(status_code)
    raw = data.get('detail')
    if raw is None and isinstance(data.get('error'), str):
        raw = data['error']
    message = _first_message(raw if raw is not None else data) or status_code_name(status_code)
    return build_error(status_code, code, message)


class ErrorEnvelopeMiddleware:
    """
    Adds the envelope to JSON error responses under ``/api/`` that do not have
    it yet. Place directly after ``RequestIdMiddleware`` so it also sees
    rejections from later middleware (security, PII masking, tenant routing).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code < 400
            or not request.path.startswith('/api/')
            or getattr(response, 'streaming', False)
            or 'json' not in response.get('Content-Type', '')
            or len(response.content) > MAX_REWRITE_BYTES
        ):
            return response
        try:
            data = json.loads(response.content or b'null')
        except (ValueError, UnicodeDecodeError):
            return response
        if not isinstance(data, dict) or _has_envelope(data):
            return response

        data.update(_generic_envelope(data, response.status_code))
        response.content = json.dumps(data, default=str, ensure_ascii=False, separators=(',', ':')).encode()
        if response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))
        return response


# ── Django-level handlers (URL not routed, unhandled exception) ──────────────

def page_not_found(request, exception):
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': 'Not found.', **build_error(404, 'not_found', 'Not found.')}, status=404)
    return django_defaults.page_not_found(request, exception)


def server_error(request):
    if request.path.startswith('/api/'):
        message = 'Internal server error. Quote the request id when contacting support.'
        return JsonResponse({'detail': message, **build_error(500, 'server_error', message)}, status=500)
    return django_defaults.server_error(request)
