import json
import logging
import re
import threading

from django.conf import settings

from .privacy_guard import PIIMaskingPipeline

logger = logging.getLogger(__name__)

_pipeline = PIIMaskingPipeline()

_MASK_METHODS = {"POST", "PATCH", "PUT"}
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)
# Machine ids whose digits can look like a card or account number: a UUID, or a viewing slot id
# ``<listing uuid>@YYYYMMDDTHHMMZ`` (KI-125: about 1 slot id in 570 was masked and refused as unknown).
_MACHINE_ID = re.compile(_UUID.pattern + r'(?:@\d{8}T\d{4}Z)?', re.I)
_DEFAULT_EXEMPT = ["/api/v1/compliance/", "/api/v1/auth/"]


def _compile_allowlist(rules) -> list[tuple]:
    """[{path, methods, fields}] → [(regex, {METHODS}, {fields})] (T-1033)."""
    compiled = []
    for rule in rules or ():
        compiled.append((re.compile(rule['path']),
                         {m.upper() for m in rule.get('methods', _MASK_METHODS)},
                         frozenset(rule['fields'])))
    return compiled


def _fire_detection_events(detections, org_id, path):
    """Write PIIDetectionEvent rows in a daemon thread — never blocks the request."""
    try:
        from apps.compliance.models import PIIDetectionEvent  # lazy — model added in Task 3
        for d in detections:
            PIIDetectionEvent.objects.create(
                org_id=org_id,
                source=PIIDetectionEvent.Source.API_SUBMISSION,
                field_path=path,
                pattern_name=d.pattern_name,
                masked_value=d.masked_value,
            )
    except Exception:
        logger.warning("PIIMaskingMiddleware: could not persist PIIDetectionEvent", exc_info=True)


def _mask_strings_recursive(obj, pipeline):
    """Recursively mask all string values in a parsed JSON structure."""
    if isinstance(obj, str):
        if _MACHINE_ID.fullmatch(obj):        # an id: its digits can look like a card number
            return obj, []
        result = pipeline.mask(obj)
        return result.masked_text, result.detections
    if isinstance(obj, dict):
        masked = {}
        all_detections = []
        for k, v in obj.items():
            masked[k], dets = _mask_strings_recursive(v, pipeline)
            all_detections.extend(dets)
        return masked, all_detections
    if isinstance(obj, list):
        masked = []
        all_detections = []
        for item in obj:
            m, dets = _mask_strings_recursive(item, pipeline)
            masked.append(m)
            all_detections.extend(dets)
        return masked, all_detections
    return obj, []


def is_exempt(path: str, prefixes=None) -> bool:
    if prefixes is None:
        prefixes = getattr(settings, 'PRIVACY_MASK_EXEMPT_PATHS', _DEFAULT_EXEMPT)
    return any(path.startswith(p) for p in prefixes)


def allowed_fields(method: str, path: str, compiled=None) -> frozenset:
    """Top-level keys that may carry raw contact PII on this endpoint (T-1033)."""
    if compiled is None:
        compiled = _compile_allowlist(getattr(settings, 'PRIVACY_MASK_FIELD_ALLOWLIST', ()))
    allowed = set()
    for regex, methods, fields in compiled:
        if method in methods and regex.match(path):
            allowed |= fields
    return frozenset(allowed)


def mask_fields(payload: dict, allowed) -> tuple[dict, list]:
    """Allow-listed top-level fields pass through; every other value (bio, notes) is masked."""
    rest, detections = _mask_strings_recursive({k: v for k, v in payload.items() if k not in allowed}, _pipeline)
    return {k: (v if k in allowed else rest[k]) for k, v in payload.items()}, detections


def report_detections(request, detections) -> None:
    org_id = getattr(getattr(request, 'org', None), 'id', None)
    threading.Thread(target=_fire_detection_events, args=(detections, org_id, request.path_info), daemon=True).start()


class PIIMaskingMiddleware:
    """
    Intercepts POST/PATCH/PUT requests and masks PII from the JSON body
    before the view processes it (forms: ``apps.compliance.parsers``). Fail-open:
    any error logs WARNING and passes the original request through unmodified.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_paths: list[str] = getattr(settings, 'PRIVACY_MASK_EXEMPT_PATHS', _DEFAULT_EXEMPT)
        self.field_allowlist = _compile_allowlist(getattr(settings, 'PRIVACY_MASK_FIELD_ALLOWLIST', ()))

    def __call__(self, request):
        if request.method in _MASK_METHODS:
            self._process(request)
        return self.get_response(request)

    def _process(self, request):
        path = request.path_info
        if is_exempt(path, self.exempt_paths) or 'application/json' not in request.META.get('CONTENT_TYPE', '').lower():
            return

        try:
            raw = request.body  # reads + caches in request._body
            if not raw:
                return

            payload = json.loads(raw)
            if isinstance(payload, dict):
                masked, detections = mask_fields(payload, allowed_fields(request.method, path, self.field_allowlist))
            else:
                masked, detections = _mask_strings_recursive(payload, _pipeline)
            if not detections:
                return

            # Replace the cached body so DRF and views see the masked payload.
            request._body = json.dumps(masked).encode('utf-8')
            request.__dict__.pop('body', None)  # clear cached_property if set
            report_detections(request, detections)

        except Exception:
            logger.warning("PIIMaskingMiddleware: masking failed, passing original body", exc_info=True)
