"""
Query-parameter parsing for API views (T-1090c, GAP-031).

Bad input is the client's fault, so it must produce a field-level 400 (which
the ADR-017 envelope reports as ``validation_error`` with ``fields``), never a
500 from a bare ``int()`` or a negative queryset slice.

    limit, offset = limit_offset(request.query_params, default=50, maximum=200)
    min_price = int_param(params, 'min_price')
    ptype = choice_param(params, 'type', Property.PropertyType.values)
"""
from rest_framework.exceptions import ValidationError


def _raw(params, name):
    raw = params.get(name)
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def int_param(params, name, default=None, *, min_value=0, max_value=None, clamp=True):
    """
    Optional integer. Thousands separators are accepted (``5,000,000``).
    Values above ``max_value`` are clamped when ``clamp`` (the long-standing
    behaviour of ``limit`` params), otherwise rejected. Values below
    ``min_value`` are always rejected.
    """
    raw = _raw(params, name)
    if raw is None:
        return default
    try:
        value = int(raw.replace(',', ''))
    except ValueError:
        raise ValidationError({name: 'Must be a whole number.'})
    if min_value is not None and value < min_value:
        raise ValidationError({
            name: 'Must not be negative.' if min_value == 0 else f'Must be at least {min_value}.',
        })
    if max_value is not None and value > max_value:
        if not clamp:
            raise ValidationError({name: f'Must be at most {max_value}.'})
        value = max_value
    return value


def limit_offset(params, *, default=50, maximum=200):
    """``(limit, offset)``: limit in 1..maximum (clamped above), offset ≥ 0."""
    limit = int_param(params, 'limit', default, min_value=1, max_value=maximum)
    offset = int_param(params, 'offset', 0, min_value=0)
    return limit, offset


def choice_param(params, name, choices, default=None):
    """Optional value that must be one of ``choices`` (case-sensitive)."""
    raw = _raw(params, name)
    if raw is None:
        return default
    allowed = list(choices)
    if raw not in allowed:
        raise ValidationError({name: f"Must be one of: {', '.join(sorted(map(str, allowed)))}."})
    return raw
