"""
``Idempotency-Key`` support for POSTs that create things (T-1090d, ADR-018).

Opt in per handler::

    class CreateCheckoutView(APIView):
        @idempotent
        def post(self, request, deal_id): ...

A client that times out can resend the same request with the same key and
gets the stored outcome instead of a second lead, booking or checkout:

* no header                        → the view runs normally (opt-in for clients)
* first use of a key               → the view runs; a 2xx outcome is stored
* same key, same request, finished → stored status + body replayed,
                                     ``Idempotent-Replayed: true``
* same key, same request, running  → 409 ``idempotency_request_in_progress``
* same key, different request      → 422 ``idempotency_key_reused``

Keys are scoped to the authenticated user, so two users cannot collide or read
each other's stored responses. Only successes are stored: errors (4xx, 5xx,
exceptions) free the key, so the client can fix the request or retry later
with the same key. Re-running a failed request has no side effect to repeat.
The decorator wraps the handler, which DRF calls after authentication,
permission and throttle checks.
"""
import functools
import hashlib
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

logger = logging.getLogger(__name__)

HEADER = 'Idempotency-Key'
REPLAY_HEADER = 'Idempotent-Replayed'
_KEY_RE = re.compile(r'^[\x21-\x7e]{1,255}$')      # printable ASCII, no spaces
LEASE = timedelta(minutes=5)                        # an in-flight request older than this is presumed dead

# For @extend_schema(parameters=[IDEMPOTENCY_PARAMETER]) on decorated handlers.
IDEMPOTENCY_PARAMETER = OpenApiParameter(
    HEADER, str, OpenApiParameter.HEADER, required=False,
    description='Optional, up to 255 printable ASCII characters (a UUID is ideal). '
                'Resending the same request with the same key within the retention '
                'window returns the original response instead of acting twice (ADR-018).',
)


def _ttl():
    return timedelta(hours=getattr(settings, 'IDEMPOTENCY_TTL_HOURS', 24))


def _request_hash(request) -> str:
    try:
        body = json.dumps(request.data, sort_keys=True, cls=DjangoJSONEncoder, default=str)
    except (TypeError, ValueError):
        body = repr(request.data)
    raw = f'{request.method}\n{request.path}\n{body}'
    for name, files in sorted(request.FILES.lists()):        # the JSON above has names only, and one file per field
        for f in files:
            raw += f'\n{name}:{hashlib.sha256(b"".join(f.chunks())).hexdigest()}'
            f.seek(0)
    return hashlib.sha256(raw.encode()).hexdigest()


def _stored(status_code: int) -> bool:
    return 200 <= status_code < 300


def _error(status_code: int, code: str, message: str) -> Response:
    # The ADR-017 envelope middleware adds error_info/request_id.
    return Response({'detail': message, 'code': code}, status=status_code)


def _replay(record) -> Response:
    try:
        data = json.loads(record.response_body) if record.response_body else None
    except ValueError:
        data = None
    response = Response(data, status=record.response_status)
    response[REPLAY_HEADER] = 'true'
    return response


def _claim(scope, key, request_hash):
    """
    Returns ``(record, None)`` when this request owns the key and must run the
    view, or ``(None, response)`` to return without running it.
    """
    from apps.core.models import IdempotencyRecord

    now = timezone.now()
    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                scope=scope, key=key, request_hash=request_hash,
                locked_until=now + LEASE, expires_at=now + _ttl(),
            )
        return record, None
    except IntegrityError:
        pass

    existing = IdempotencyRecord.objects.filter(scope=scope, key=key).first()
    if existing is None:            # purged between the insert and the read
        return _claim(scope, key, request_hash)

    if existing.expires_at <= now:
        # Past retention: the key is free again. Conditional delete, then retry.
        IdempotencyRecord.objects.filter(pk=existing.pk, expires_at__lte=now).delete()
        return _claim(scope, key, request_hash)

    if existing.request_hash != request_hash:
        return None, _error(422, 'idempotency_key_reused',
                            'This Idempotency-Key was already used for a different request.')

    if existing.status == IdempotencyRecord.Status.COMPLETED:
        return None, _replay(existing)

    # In progress. Take over only a stale lease (the first request died).
    taken = IdempotencyRecord.objects.filter(
        pk=existing.pk, status=IdempotencyRecord.Status.IN_PROGRESS, locked_until__lte=now,
    ).update(locked_until=now + LEASE)
    if taken:
        existing.refresh_from_db()
        return existing, None
    return None, _error(409, 'idempotency_request_in_progress',
                        'A request with this Idempotency-Key is still being processed. Retry shortly.')


def _finish(record, response):
    from apps.core.models import IdempotencyRecord

    if not _stored(response.status_code):
        IdempotencyRecord.objects.filter(pk=record.pk).delete()
        return
    try:
        body = json.dumps(getattr(response, 'data', None), cls=DjangoJSONEncoder)
    except (TypeError, ValueError):
        logger.warning('idempotency: response for key %s is not JSON-serialisable; not stored', record.key)
        IdempotencyRecord.objects.filter(pk=record.pk).delete()
        return
    record.status = IdempotencyRecord.Status.COMPLETED
    record.response_status = response.status_code
    record.response_body = body
    record.save(update_fields=['status', 'response_status', 'response_body'])


def idempotent(handler):
    """Decorator for a DRF handler method (``post``, ``create``)."""

    @functools.wraps(handler)
    def wrapper(view, request, *args, **kwargs):
        key = request.headers.get(HEADER)
        if key is None:
            return handler(view, request, *args, **kwargs)
        key = key.strip()
        if not _KEY_RE.match(key):
            raise ValidationError({HEADER: 'Must be 1–255 printable ASCII characters without spaces.'})
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return handler(view, request, *args, **kwargs)   # nothing to scope by

        record, early = _claim(f'user:{user.pk}', key, _request_hash(request))
        if early is not None:
            return early
        try:
            response = handler(view, request, *args, **kwargs)
        except BaseException:
            from apps.core.models import IdempotencyRecord
            IdempotencyRecord.objects.filter(pk=record.pk).delete()
            raise
        _finish(record, response)
        return response

    return wrapper


def purge_expired() -> int:
    from apps.core.models import IdempotencyRecord
    deleted, _ = IdempotencyRecord.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted
