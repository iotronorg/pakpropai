"""
Explicit tool-loop runtime (T-2010, AI spec §3.1, ADR-029).

The provider proposes tool calls (``AIBackend.step``); the runtime validates
them against the registry (``apps.ai.tool_registry``), executes them with the
request context already set by the caller (``apps.ai.tools.set_context``), and
feeds the results back, for at most ``AI_MAX_TOOL_STEPS`` model calls.

* Every model call reports its own usage (``apps.ai.usage``), so multi-step
  turns are fully counted (KI-006 follow-up: AFC used to report the last round only).
* Every tool call is audited: name, validated args, outcome, duration,
  side-effect class (``TurnResult.tool_calls``, logged and stored on the turn's
  ``AIInteraction``).
* Tools marked ``terminal`` (agent contact, audit PDF) end the turn with the
  tool's own ``whatsapp_summary``/``message`` verbatim: the model never
  re-phrases database facts such as agent phone numbers.
* Tools with ``requires_confirmation`` are never executed here: the call becomes a
  ``PendingAction`` and the turn ends with its summary verbatim (T-2012, ADR-030).
* Invalid calls (unknown tool, disabled tool, bad arguments) become error
  results the model can recover from; a tool that raises becomes
  ``{'error': ...}`` and is logged.
* Failover: ``apps.ai.llm`` restarts the turn on the next provider only while no
  committing tool (side effect other than none/session) has run.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from django.conf import settings

from . import grounding as G
from . import tool_registry as R

logger = logging.getLogger(__name__)

GROUNDING_ERROR_REPLY = "I could not verify the details of that answer. Please try again in a moment."
STEP_LIMIT_REPLY = "I couldn't complete that request. Please try rephrasing."


class TurnCommitted(RuntimeError):
    """The provider failed after a committing tool ran; the turn must not be retried."""


@dataclass
class TurnResult:
    text: str
    model: str = ''
    steps: int = 0
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    terminal_tool: str = ''
    grounding: str = ''            # passed | corrected | fallback | error (T-2013)
    grounding_codes: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    results: list | None = None    # listing ids of the last search this turn
    criteria: dict | None = None
    matches: dict = field(default_factory=dict)    # listing id -> score, reasons, tradeoffs


def _history_messages(history: list) -> list:
    out = []
    for item in history or ():
        text = (item.get('text') or '').strip()
        if not text:
            continue
        out.append({'role': 'assistant' if item.get('role') == 'model' else 'user', 'text': text})
    return out


def _summary(result) -> str | None:
    if isinstance(result, dict):
        return result.get('whatsapp_summary') or result.get('message')
    return None


def _execute(spec: R.ToolSpec, args: dict):
    started = time.monotonic()
    try:
        if spec.requires_confirmation:
            from .confirmation import propose
            result = propose(spec, args)
        else:
            result = spec.fn(**args)
        ok = not (isinstance(result, dict) and result.get('error'))
    except Exception as exc:                     # a tool bug must not end the conversation
        logger.error("tool '%s' raised: %s", spec.name, exc, exc_info=True)
        result, ok = {'error': 'The tool failed. Please try again later.'}, False
    return result, ok, int((time.monotonic() - started) * 1000)


def run_turn(backend, message: str, history: list, specs: list, system: str, *,
             max_steps: int | None = None) -> TurnResult:
    max_steps = max_steps or getattr(settings, 'AI_MAX_TOOL_STEPS', 5)
    by_name = {s.name: s for s in specs}
    schemas = [s.schema for s in specs]
    messages = _history_messages(history) + [{'role': 'user', 'text': message}]
    turn = TurnResult(text='')
    committed = False
    evidence = G.Evidence(message, history, system)

    for step_no in range(1, max_steps + 1):
        try:
            step = backend.step(system, messages, schemas, timeout=settings.AI_TIMEOUT_S)
        except Exception as exc:
            if committed:
                raise TurnCommitted(str(exc)) from exc
            raise
        turn.steps, turn.model = step_no, step.model
        turn.prompt_tokens += step.prompt_tokens
        turn.completion_tokens += step.completion_tokens
        if not step.tool_calls:
            turn.text = step.text
            _ground(turn, evidence, backend, system, messages)
            return turn

        messages.append({'role': 'assistant', 'text': step.text, 'tool_calls': step.tool_calls,
                         'raw': step.raw, 'provider': getattr(backend, 'name', '')})
        terminal, proposed = None, False
        for call in step.tool_calls:
            record = {'id': call.id, 'name': call.name, 'step': step_no}
            spec = by_name.get(call.name)
            if proposed:                         # the user must answer the proposal first
                result = {'error': 'Waiting for the user to confirm the previous action.'}
                record.update(status='skipped', reason='awaiting_confirmation')
            elif spec is None:
                result = {'error': f'Unknown or unavailable tool: {call.name}'}
                record.update(status='rejected', reason='unknown_tool')
            else:
                try:
                    args = R.validate_args(spec, call.args)
                except R.ToolArgsInvalid as exc:
                    result = {'error': f'Invalid arguments: {exc}'}
                    record.update(status='rejected', reason='invalid_args', side_effect=spec.side_effect)
                else:
                    result, ok, ms = _execute(spec, args)
                    proposed = isinstance(result, dict) and bool(result.get('confirmation_required'))
                    committed = committed or (ok and spec.commits and not proposed)
                    if proposed:
                        record['pending_action'] = result.get('pending_action_id')
                    record.update(status='proposed' if proposed else 'ok' if ok else 'error', args=_redact(args), ms=ms,
                                  side_effect=spec.side_effect)
                    if proposed or (spec.terminal and ok and _summary(result)):
                        terminal = (spec.name, _summary(result))
            if record['status'] == 'ok':
                evidence.add(result)
                if (isinstance(result, dict) and isinstance(result.get('properties'), list)
                        and not result.get('detail')):          # a search, not one listing's facts
                    turn.results = [str(r['id']) for r in result['properties'] if r.get('id')]
                    turn.criteria = result['criteria'] if 'criteria' in result else dict(args)
                    turn.matches.update({str(r['id']): {k: r[k] for k in ('match_score', 'reasons', 'tradeoffs')}
                                         for r in result['properties'] if r.get('id') and 'match_score' in r})
            turn.tool_calls.append(record)
            logger.info('ai.tool %s', json.dumps(record, default=str)[:500])
            messages.append({'role': 'tool', 'id': call.id, 'name': call.name, 'result': result})
        if terminal:
            turn.terminal_tool, turn.text = terminal
            return turn

    logger.warning('ai.runtime: step limit (%d) reached without a final answer', max_steps)
    turn.text = STEP_LIMIT_REPLY
    return turn


def _ground(turn: TurnResult, evidence, backend, system: str, messages: list) -> None:
    """Check the model's reply; one regeneration, then facts only (T-2013, ADR-032)."""
    if not turn.text.strip():
        return
    try:
        verdict = G.check(turn.text, evidence)
        turn.citations, turn.grounding = verdict.cited, 'passed'
        if verdict.ok:
            return
        turn.grounding_codes = verdict.codes
        logger.warning('ai.grounding violations=%s', verdict.codes)
        retry = [*messages, {'role': 'assistant', 'text': turn.text},
                 {'role': 'user', 'text': G.correction(verdict)}]
        try:
            step = backend.step(system, retry, [], timeout=settings.AI_TIMEOUT_S)
            turn.steps += 1
            turn.prompt_tokens += step.prompt_tokens
            turn.completion_tokens += step.completion_tokens
            second = G.check(step.text, evidence) if step.text.strip() and not step.tool_calls else None
        except Exception as exc:
            logger.warning('ai.grounding regeneration failed: %s', type(exc).__name__)
            second = None
        if second is not None and second.ok:
            turn.text, turn.citations, turn.grounding = step.text, second.cited, 'corrected'
            return
        turn.text, turn.grounding = G.fallback(evidence), 'fallback'
        turn.citations = [l.id for l in evidence.listings()[:5]]
        G.record_violation(verdict, evidence)
    except Exception as exc:                  # never send an unchecked reply
        logger.error('ai.grounding check failed: %s', exc, exc_info=True)
        turn.text, turn.grounding = GROUNDING_ERROR_REPLY, 'error'


_REDACT = {'phone', 'cnic', 'national_id', 'email', 'name', 'owner_name'}


def _redact(args: dict) -> dict:
    """Audit copy of the arguments: contact-like values are masked."""
    return {k: ('***' if k in _REDACT and v else v) for k, v in args.items()}
