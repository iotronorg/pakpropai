"""
Per-request LLM token usage capture.

Backends call ``record_usage()`` after every provider call. The orchestration
layer opens a capture with ``start_capture()`` and reads the accumulated totals
with ``current_usage()``. A ContextVar keeps concurrent requests (threads, Celery
workers, async consumers) isolated from each other.

Usage figures come from the provider response (Gemini ``usage_metadata``,
OpenAI-compatible ``usage``). When a provider does not report usage, nothing is
recorded. Values are never estimated or fabricated.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass
class UsageTotals:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    calls: int = 0
    models: list[str] = field(default_factory=list)
    records: list[tuple] = field(default_factory=list)   # (model, in, out, cached, reported) per call
    reported: bool = False   # True once at least one provider call reported usage

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def model(self) -> str:
        return self.models[-1] if self.models else ''


_current: contextvars.ContextVar[UsageTotals | None] = contextvars.ContextVar(
    'ai_usage_totals', default=None,
)


def start_capture() -> UsageTotals:
    totals = UsageTotals()
    _current.set(totals)
    return totals


def current_usage() -> UsageTotals:
    totals = _current.get()
    return totals if totals is not None else UsageTotals()


def record_usage(model: str, tokens_in: int | None, tokens_out: int | None,
                 cached_tokens: int | None = 0) -> None:
    """Add one provider call's usage to the active capture (no-op without a capture)."""
    totals = _current.get()
    if totals is None:
        return
    totals.calls += 1
    if model:
        totals.models.append(model)
    reported = not (tokens_in is None and tokens_out is None)
    totals.records.append((model or '', int(tokens_in or 0), int(tokens_out or 0),
                           int(cached_tokens or 0), reported))
    if not reported:
        return
    totals.tokens_in += int(tokens_in or 0)
    totals.tokens_out += int(tokens_out or 0)
    totals.cached_tokens += int(cached_tokens or 0)
    totals.reported = True


def record_gemini_response(model: str, response) -> None:
    """Extract usage from a google-genai response object."""
    meta = getattr(response, 'usage_metadata', None)
    if meta is None:
        record_usage(model, None, None)
        return
    out = getattr(meta, 'candidates_token_count', None)
    thoughts = getattr(meta, 'thoughts_token_count', None)
    if isinstance(thoughts, int) and thoughts:      # thinking tokens are billed as output
        out = (out or 0) + thoughts
    record_usage(
        model,
        getattr(meta, 'prompt_token_count', None),
        out,
        getattr(meta, 'cached_content_token_count', None),
    )


def record_openai_response(model: str, response) -> None:
    """Extract usage from an OpenAI-compatible (Ollama) chat completion."""
    usage = getattr(response, 'usage', None)
    if usage is None:
        record_usage(model, None, None)
        return
    cached = getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None)
    record_usage(
        model,
        getattr(usage, 'prompt_tokens', None),
        getattr(usage, 'completion_tokens', None),
        cached if isinstance(cached, int) else 0,
    )
