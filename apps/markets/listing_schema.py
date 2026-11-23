"""
Market listing attribute schema (T-1070e, FR-LST-004, ADR-023 addendum).

``Market.listing_schema`` describes the market-specific keys a listing may
carry in ``Property.attributes``. The format is deliberately small (no
JSON-Schema dependency, easy to edit in admin, easy to show in a form):

    {
      "fields": {
        "possession":   {"type": "enum", "values": ["available", "not_available"],
                         "label": "Possession"},
        "rera_permit":  {"type": "string", "max_length": 40, "pattern": "^[A-Za-z0-9-]+$"},
        "lease_years":  {"type": "integer", "min": 0, "max": 999},
        "corner":       {"type": "boolean"},
        "service_fee":  {"type": "number", "min": 0}
      },
      "additional": "reject"        # or "allow": keys not in `fields`
    }

An empty schema (``{}``) means "no market rules": only the generic size cap
in ``Property.v2_errors`` applies.
"""
import re

TYPES = ('string', 'integer', 'number', 'boolean', 'enum')
_FIELD_KEYS = {
    'string':  {'type', 'label', 'max_length', 'pattern'},
    'integer': {'type', 'label', 'min', 'max'},
    'number':  {'type', 'label', 'min', 'max'},
    'boolean': {'type', 'label'},
    'enum':    {'type', 'label', 'values'},
}
_KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,49}$')
MAX_STRING = 500


def schema_errors(schema) -> list[str]:
    """Problems with a schema definition itself (Market.clean)."""
    if schema in (None, {}):
        return []
    if not isinstance(schema, dict):
        return ['Must be an object.']
    errors = []
    unknown = set(schema) - {'fields', 'additional'}
    if unknown:
        errors.append(f"Unknown top-level keys: {', '.join(sorted(unknown))}.")
    if schema.get('additional', 'reject') not in ('reject', 'allow'):
        errors.append('"additional" must be "reject" or "allow".')
    fields = schema.get('fields', {})
    if not isinstance(fields, dict):
        return errors + ['"fields" must be an object.']
    for key, spec in fields.items():
        where = f'fields.{key}'
        if not _KEY_RE.match(str(key)):
            errors.append(f'{where}: key must be snake_case, at most 50 characters.')
        if not isinstance(spec, dict) or spec.get('type') not in TYPES:
            errors.append(f"{where}: type must be one of {', '.join(TYPES)}.")
            continue
        extra = set(spec) - _FIELD_KEYS[spec['type']]
        if extra:
            errors.append(f"{where}: unsupported options {', '.join(sorted(extra))}.")
        if spec['type'] == 'enum':
            values = spec.get('values')
            if not isinstance(values, list) or not values or \
                    not all(isinstance(v, str) and v for v in values) or len(set(values)) != len(values):
                errors.append(f'{where}: values must be a non-empty list of distinct strings.')
        if spec['type'] in ('integer', 'number'):
            lo, hi = spec.get('min'), spec.get('max')
            for name, bound in (('min', lo), ('max', hi)):
                if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float))):
                    errors.append(f'{where}: {name} must be a number.')
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
                errors.append(f'{where}: min must not exceed max.')
        if spec['type'] == 'string':
            ml = spec.get('max_length')
            if ml is not None and (isinstance(ml, bool) or not isinstance(ml, int)
                                   or not 1 <= ml <= MAX_STRING):
                errors.append(f'{where}: max_length must be 1-{MAX_STRING}.')
            if spec.get('pattern') is not None:
                try:
                    re.compile(spec['pattern'])
                except (re.error, TypeError):
                    errors.append(f'{where}: pattern is not a valid regular expression.')
    return errors


def attribute_errors(schema, attributes: dict) -> dict:
    """Per-key errors for listing attributes under a market schema ({} = valid)."""
    if not schema or not isinstance(attributes, dict):
        return {}
    fields = schema.get('fields') or {}
    errors = {}
    for key, value in attributes.items():
        spec = fields.get(key)
        if spec is None:
            if schema.get('additional', 'reject') == 'reject':
                errors[key] = 'Not an attribute of this market.'
            continue
        if value is None:
            continue                                  # explicit "unknown"
        msg = _value_error(spec, value)
        if msg:
            errors[key] = msg
    return errors


def _value_error(spec: dict, value) -> str | None:
    kind = spec['type']
    if kind == 'boolean':
        return None if isinstance(value, bool) else 'Must be true or false.'
    if kind == 'enum':
        values = spec.get('values') or []
        return None if value in values else f"Must be one of: {', '.join(values)}."
    if kind == 'string':
        if not isinstance(value, str):
            return 'Must be a string.'
        if len(value) > spec.get('max_length', MAX_STRING):
            return f"At most {spec.get('max_length', MAX_STRING)} characters."
        if spec.get('pattern') and not re.fullmatch(spec['pattern'], value):
            return 'Invalid format.'
        return None
    # integer / number
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 'Must be a number.'
    if kind == 'integer' and not float(value).is_integer():
        return 'Must be a whole number.'
    if spec.get('min') is not None and value < spec['min']:
        return f"Must be at least {spec['min']}."
    if spec.get('max') is not None and value > spec['max']:
        return f"Must be at most {spec['max']}."
    return None
