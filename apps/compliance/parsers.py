"""
Form bodies, masked like JSON bodies (KI-094).

The middleware cannot rewrite a multipart body cheaply, so the DRF parsers mask the
text fields after parsing. Files are untouched. Every API view parses forms with these.
"""
from rest_framework.parsers import FormParser, MultiPartParser

from .middleware import allowed_fields, is_exempt, mask_fields, report_detections


def _mask(data, parser_context):
    """``data``: the QueryDict of a form."""
    request = (parser_context or {}).get('request')
    if request is None or is_exempt(request.path_info):
        return data
    allowed = allowed_fields(request.method, request.path_info)
    masked, detections = mask_fields({k: data.getlist(k) for k in data}, allowed)
    if not detections:
        return data
    out = data.copy()
    for key, values in masked.items():
        out.setlist(key, values)
    report_detections(request, detections)
    return out


class MaskedFormParser(FormParser):
    def parse(self, stream, media_type=None, parser_context=None):
        return _mask(super().parse(stream, media_type, parser_context), parser_context)


class MaskedMultiPartParser(MultiPartParser):
    def parse(self, stream, media_type=None, parser_context=None):
        result = super().parse(stream, media_type, parser_context)
        result.data = _mask(result.data, parser_context)
        return result
