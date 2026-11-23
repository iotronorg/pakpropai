"""
LLM router (T-1120, ADR-028): the one way product code calls a model.

* ``complete(prompt_name, values, …)``: renders a registered prompt
  (``apps.ai.prompts``), tries the providers in ``AI_PROVIDER_ORDER``
  (unconfigured ones skipped; one with an open circuit breaker skipped), with
  ``AI_TIMEOUT_S`` per call, and logs an ``AIInteraction`` row carrying the
  prompt name / version / digest and the provider that answered.
* ``complete_json`` parses the JSON object (or array) out of the reply.
* ``chat(…)`` is the tool-using conversational turn with the same failover.
* ``vision(prompt_name, image, …)`` for image prompts (OCR).

Failover happens on provider errors (network, quota, 5xx, timeouts), never on
an empty or unparsable answer. If every provider fails, :class:`LLMUnavailable`
is raised and callers keep their existing fallbacks (canned replies,
deterministic scores). Provider token usage is still recorded by the backends
(``apps.ai.usage``), so the token governor is unchanged.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from . import prompts as P
from .backends import PROVIDERS, make_backend

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """No configured provider produced an answer."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt: P.Rendered | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ms: int = 0
    tool_calls: list = None
    steps: int = 0


# ── Providers + circuit breaker ───────────────────────────────────────────────

def provider_order() -> list[str]:
    order = [p for p in getattr(settings, 'AI_PROVIDER_ORDER', None) or [settings.AI_BACKEND]
             if p in PROVIDERS]
    return order or ['gemini']


def _breaker_key(name: str) -> str:
    return f'ai:breaker:{name}'


def breaker_open(name: str) -> bool:
    return int(cache.get(_breaker_key(name)) or 0) >= settings.AI_BREAKER_FAILURES


def _fail(name: str):
    key = _breaker_key(name)
    cache.add(key, 0, settings.AI_BREAKER_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, settings.AI_BREAKER_SECONDS)


def _ok(name: str):
    cache.delete(_breaker_key(name))


def _candidates():
    """Configured providers in order; those with an open breaker go last (still tried)."""
    ready, tripped = [], []
    for name in provider_order():
        backend = make_backend(name)
        try:
            if not backend.is_configured():
                continue
        except Exception:                      # e.g. config lookup failure
            continue
        (tripped if breaker_open(name) else ready).append((name, backend))
    return ready + tripped


def any_configured() -> bool:
    return bool(_candidates())


def _run(op, *, what: str):
    """Try providers in order; a turn that already ran a committing tool is never retried."""
    from .runtime import TurnCommitted
    errors = []
    for name, backend in _candidates():
        started = time.monotonic()
        try:
            out = op(backend)
        except TurnCommitted:
            _fail(name)
            logger.error('llm.%s provider=%s failed after a committing tool ran; not failing over',
                         what, name)
            raise
        except Exception as exc:              # provider failure → next one
            _fail(name)
            errors.append(f'{name}: {type(exc).__name__}')
            logger.warning('llm.%s provider=%s failed: %s', what, name, type(exc).__name__)
            continue
        _ok(name)
        return name, backend, out, int((time.monotonic() - started) * 1000)
    raise LLMUnavailable(f'no AI provider answered ({"; ".join(errors) or "none configured"})')


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(result: LLMResult, *, interaction_type: str, user=None, input_preview: str = '',
         cached: bool = False):
    from .models import AIInteraction
    ref = result.prompt.ref if result.prompt else {}
    try:
        AIInteraction.objects.create(
            user=user if getattr(user, 'pk', None) else None,
            interaction_type=interaction_type, model_used=result.model[:50],
            provider=result.provider, prompt_tokens=result.prompt_tokens,
            response_tokens=result.completion_tokens, was_cached=cached, response_ms=result.ms,
            prompt_name=ref.get('prompt_name', ''), prompt_version=ref.get('prompt_version'),
            prompt_digest=ref.get('prompt_digest', ''),
            input_data={'prompt_preview': input_preview[:200]},
            output_data={'preview': result.text[:200],
                         **({'tool_calls': result.tool_calls, 'steps': result.steps}
                            if result.tool_calls is not None else {})})
    except Exception as exc:                   # logging must never break the call
        logger.warning('AIInteraction log failed: %s', exc)


# ── Public API ────────────────────────────────────────────────────────────────

def render(prompt_name: str, values: dict | None = None, *, country: str | None = None) -> P.Rendered:
    """Render with market variables (market_name, currency, …) filled in unless given."""
    prompt = P.get(prompt_name)
    merged = {**P.market_vars(country), **(values or {})}
    return prompt.render(**{k: merged[k] for k in prompt.variables if k in merged})


def complete(prompt_name: str, values: dict | None = None, *, country: str | None = None,
             interaction_type: str = 'intent_classify', user=None, system: str = '',
             max_tokens: int = 512, temperature: float = 0.2, json_mode: bool = False,
             cache_ttl: int = 0) -> LLMResult:
    rendered = render(prompt_name, values, country=country)
    return _complete(rendered.text, rendered, interaction_type=interaction_type, user=user,
                     system=system, max_tokens=max_tokens, temperature=temperature,
                     json_mode=json_mode, cache_ttl=cache_ttl)


def complete_text(text: str, **kwargs) -> LLMResult:
    """Unregistered prompt (deprecated path: backends.generate). Logged without a prompt ref."""
    return _complete(text, None, interaction_type=kwargs.pop('interaction_type', 'intent_classify'),
                     **kwargs)


def _complete(text, rendered, *, interaction_type, user=None, system='', max_tokens=512,
              temperature=0.2, json_mode=False, cache_ttl=0) -> LLMResult:
    import hashlib
    key = None
    if cache_ttl:
        key = 'ai:resp:v2:' + hashlib.sha256(f'{system}\x00{text}\x00{json_mode}'.encode()).hexdigest()
        hit = cache.get(key)
        if hit:
            result = LLMResult(text=hit, provider='cache', model='cache', prompt=rendered)
            _log(result, interaction_type=interaction_type, user=user, input_preview=text, cached=True)
            return result
    name, backend, comp, ms = _run(
        lambda b: b.complete(text, system=system, max_tokens=max_tokens, temperature=temperature,
                             json_mode=json_mode, timeout=settings.AI_TIMEOUT_S), what='complete')
    result = LLMResult(text=comp.text, provider=name, model=comp.model, prompt=rendered,
                       prompt_tokens=comp.prompt_tokens, completion_tokens=comp.completion_tokens, ms=ms)
    if key and comp.text:
        cache.set(key, comp.text, cache_ttl)
    _log(result, interaction_type=interaction_type, user=user, input_preview=text)
    return result


def parse_json(text: str):
    """The JSON object/array in a model reply ({} if none)."""
    if not text:
        return {}
    for candidate in (text,):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for pattern in (r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', r'\{.*\}', r'\[.*\]'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1) if m.groups() else m.group(0))
            except json.JSONDecodeError:
                continue
    logger.warning('llm: could not parse JSON from reply (%d chars)', len(text))
    return {}


def complete_json(prompt_name: str, values: dict | None = None, **kwargs):
    kwargs.setdefault('json_mode', True)
    return parse_json(complete(prompt_name, values, **kwargs).text)


def vision(prompt_name: str, image_bytes: bytes, mime_type: str, values: dict | None = None, *,
           country: str | None = None, interaction_type: str = 'ocr', user=None) -> LLMResult:
    rendered = render(prompt_name, values, country=country)
    name, backend, text, ms = _run(lambda b: b.analyze_image(image_bytes, mime_type, rendered.text),
                                   what='vision')
    result = LLMResult(text=text, provider=name, model=backend.label, prompt=rendered, ms=ms)
    _log(result, interaction_type=interaction_type, user=user, input_preview=rendered.text)
    return result


def chat(message: str, history: list, tools: list, system: P.Rendered | str, *,
         user=None, interaction_type: str = 'chat', log: bool = True) -> LLMResult:
    """
    Tool-using conversational turn (T-2010): the explicit runtime loop with
    provider failover. ``tools``: ``ToolSpec`` objects (or plain callables,
    treated as committing ad-hoc tools). A provider failing after a committing
    tool ran raises ``runtime.TurnCommitted`` instead of failing over.
    """
    from . import runtime
    from .tool_registry import ToolSpec
    rendered = system if isinstance(system, P.Rendered) else None
    system_text = system.text if rendered else system
    specs = [t if isinstance(t, ToolSpec) else ToolSpec.from_callable(t) for t in tools]
    name, backend, turn, ms = _run(
        lambda b: runtime.run_turn(b, message, history, specs, system_text), what='chat')
    result = LLMResult(text=turn.text, provider=name, model=turn.model or backend.label, prompt=rendered,
                       prompt_tokens=turn.prompt_tokens, completion_tokens=turn.completion_tokens, ms=ms,
                       tool_calls=turn.tool_calls, steps=turn.steps)
    if log:                                   # the agent logs its own row (with usage totals)
        _log(result, interaction_type=interaction_type, user=user, input_preview=message)
    return result
