"""
Tool registry with per-tool policy (T-2010, AI spec §5, ADR-029).

Each conversational tool is a :class:`ToolSpec`: its JSON schema (built from
the function signature and Google-style docstring), side-effect class,
whether the tool itself demands a confirmation, the SystemConfig feature flag
that enables it, whether its user-facing summary must be returned verbatim
(``terminal``), and a per-tool timeout budget.

The runtime (``apps.ai.runtime``) asks the registry for the enabled tools,
validates every model-produced call against the schema (unknown tool, unknown
or missing arguments, wrong types → an error *result* the model can recover
from, never an exception), and executes it. The functions are looked up on
``apps.ai.tools`` at call time, so patches of that module still apply.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Side-effect classes (AI spec §5). Anything other than NONE/SESSION "commits"
# a turn: provider failover is no longer allowed once such a tool ran.
NONE, SESSION, LOW, CONTACT_SHARE, BOOKING, FINANCIAL = (
    'none', 'session', 'low', 'contact_share', 'booking', 'financial')
COMMITTING = {LOW, CONTACT_SHARE, BOOKING, FINANCIAL}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    side_effect: str = NONE
    requires_confirmation: bool = False   # proposed as a PendingAction, never run by the model (T-2012)
    flag: str = ''                        # SystemConfig feature_* key ('' = always on)
    flag_default: bool = True
    terminal: bool = False                # return whatsapp_summary / message verbatim
    timeout_ms: int = 15_000
    markets: tuple = ()                   # () = every market
    func: object = None                   # ad-hoc tools (tests, internal callers); else apps.ai.tools.<name>
    extra: dict = field(default_factory=dict)

    @property
    def fn(self):
        if self.func is not None:
            return self.func
        from apps.ai import tools as tool_module
        return getattr(tool_module, self.name)

    @classmethod
    def from_callable(cls, fn) -> 'ToolSpec':
        """A registered spec when the name is known, else a conservative ad-hoc one (commits)."""
        known = REGISTRY.get(fn.__name__)
        if known is not None and known.fn is fn:
            return known
        return cls(fn.__name__, side_effect=LOW, func=fn)

    @property
    def schema(self) -> dict:
        """Provider-neutral schema: {name, description, parameters (JSON schema)}."""
        from apps.ai.backends.base import func_to_openai_schema
        fn = func_to_openai_schema(self.fn)['function']
        return {'name': self.name, 'description': fn.get('description', ''),
                'parameters': fn.get('parameters') or {'type': 'object', 'properties': {}}}

    @property
    def commits(self) -> bool:
        return self.side_effect in COMMITTING


REGISTRY: dict[str, ToolSpec] = {s.name: s for s in (
    ToolSpec('search_listings', flag='feature_property_search'),
    ToolSpec('update_session_criteria', side_effect=SESSION, flag='feature_property_search'),
    ToolSpec('get_listing', flag='feature_property_search'),
    ToolSpec('list_property', side_effect=LOW, requires_confirmation=True, flag='feature_property_listing'),
    ToolSpec('run_fraud_check', flag='feature_scam_check'),
    ToolSpec('calculate_7e_tax', flag='feature_tax_advice'),
    ToolSpec('check_loan_eligibility', flag='feature_loan_eligibility'),
    ToolSpec('connect_to_agent', side_effect=CONTACT_SHARE, requires_confirmation=True,
             flag='feature_talk_to_agent', terminal=True),
    ToolSpec('initiate_deal_lock', side_effect=FINANCIAL, requires_confirmation=True,
             flag='feature_deal_lock', flag_default=False),
    ToolSpec('generate_property_audit', side_effect=LOW, requires_confirmation=True,
             flag='feature_property_audit', terminal=True, timeout_ms=60_000),
)}


def enabled_tools(features: dict | None = None, market: str | None = None) -> list[ToolSpec]:
    """Tools offered to the model: feature flag on and (if restricted) the market matches."""
    if features is None:
        from apps.config.services import SystemConfigService
        features = SystemConfigService.get_features()
    out = []
    for spec in REGISTRY.values():
        if spec.flag and not features.get(spec.flag, spec.flag_default):
            continue
        if spec.markets and market and market.upper() not in spec.markets:
            continue
        out.append(spec)
    return out


# ── Argument validation ───────────────────────────────────────────────────────

class ToolArgsInvalid(ValueError):
    pass


def _coerce(value, json_type: str):
    if value is None:
        return None
    if json_type == 'string':
        return value if isinstance(value, str) else str(value)
    if json_type == 'integer':
        if isinstance(value, bool):
            raise ToolArgsInvalid('expected integer')
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip('-').replace(',', '').isdigit():
            return int(value.strip().replace(',', ''))
        raise ToolArgsInvalid('expected integer')
    if json_type == 'number':
        if isinstance(value, bool):
            raise ToolArgsInvalid('expected number')
        try:
            return float(str(value).replace(',', '')) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise ToolArgsInvalid('expected number') from None
    if json_type == 'boolean':
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        raise ToolArgsInvalid('expected boolean')
    return value


def validate_args(spec: ToolSpec, args) -> dict:
    """Coerced arguments or :class:`ToolArgsInvalid`. Unknown arguments are dropped (logged)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolArgsInvalid('arguments must be an object')
    params = spec.schema['parameters']
    props = params.get('properties', {})
    unknown = set(args) - set(props)
    if unknown:
        logger.info('tool %s: dropping unknown args %s', spec.name, sorted(unknown))
    out, errors = {}, []
    for name, prop in props.items():
        if name not in args or args[name] is None:
            continue
        try:
            out[name] = _coerce(args[name], prop.get('type', 'string'))
        except ToolArgsInvalid as exc:
            errors.append(f'{name}: {exc}')
    missing = [r for r in params.get('required', []) if r not in out]
    if missing:
        errors.append(f'missing required: {", ".join(missing)}')
    if errors:
        raise ToolArgsInvalid('; '.join(errors))
    return out
