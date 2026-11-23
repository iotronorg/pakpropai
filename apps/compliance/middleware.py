import json
import logging
import re
import threading

from django.conf import settings

from .privacy_guard import PIIMaskingPipeline

logger = logging.getLogger(__name__)

_pipeline = PIIMaskingPipeline()

_MASK_METHODS = {"POST", "PATCH", "PUT"}
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


class PIIMaskingMiddleware:
    """
    Intercepts POST/PATCH/PUT requests and masks PII from the JSON body
    before the view processes it. Fail-open: any error logs WARNING and
    passes the original request through unmodified.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_paths: list[str] = getattr(
            settings, "PRIVACY_MASK_EXEMPT_PATHS", _DEFAULT_EXEMPT
        )
        self.field_allowlist = _compile_allowlist(
            getattr(settings, "PRIVACY_MASK_FIELD_ALLOWLIST", ())
        )

    def allowed_fields(self, method: str, path: str) -> frozenset:
        """Top-level JSON keys that may carry raw contact PII on this endpoint (T-1033)."""
        allowed = set()
        for regex, methods, fields in self.field_allowlist:
            if method in methods and regex.match(path):
                allowed |= fields
        return frozenset(allowed)

    def __call__(self, request):
        if request.method in _MASK_METHODS:
            self._process(request)
        return self.get_response(request)

    def _process(self, request):
        path = request.path_info
        if any(path.startswith(ep) for ep in self.exempt_paths):
            return

        content_type = request.META.get("CONTENT_TYPE", "")
        if "application/json" not in content_type:
            return

        try:
            raw = request.body  # reads + caches in request._body
            if not raw:
                return

            payload = json.loads(raw)
            allowed = self.allowed_fields(request.method, path)
            if allowed and isinstance(payload, dict):
                # Allow-listed structured fields pass through; every other
                # value (free text such as bio/notes) is still masked.
                kept = {k: v for k, v in payload.items() if k in allowed}
                rest = {k: v for k, v in payload.items() if k not in allowed}
                masked_rest, detections = _mask_strings_recursive(rest, _pipeline)
                masked_payload = {k: (kept[k] if k in kept else masked_rest[k]) for k in payload}
            else:
                masked_payload, detections = _mask_strings_recursive(payload, _pipeline)

            if not detections:
                return

            masked_bytes = json.dumps(masked_payload).encode("utf-8")
            # Replace the cached body so DRF and views see the masked payload.
            request._body = masked_bytes
            request.__dict__.pop("body", None)  # clear cached_property if set

            org_id = getattr(getattr(request, "org", None), "id", None)
            t = threading.Thread(
                target=_fire_detection_events,
                args=(detections, org_id, path),
                daemon=True,
            )
            t.start()

        except Exception:
            logger.warning("PIIMaskingMiddleware: masking failed, passing original body", exc_info=True)
