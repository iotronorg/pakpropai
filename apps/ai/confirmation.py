"""
Server-side confirmation guard for AI tools with side effects (MVP §6 item 8,
FR-AI-* "no side effect without confirmation").

The LLM cannot authorise an action on its own. A guarded tool runs only when:
  1. an earlier turn proposed exactly this action (same tool + key parameters)
     and the proposal was shown to the user, and
  2. the user's message in the current turn is an explicit affirmative.

A model that calls the tool twice in one turn, or passes made-up flags, cannot
bypass this: the proposal is bound to the turn id that created it.

Deterministic Python handlers that act on the user's literal request (e.g.
"connect me to an agent") may mark the turn as an explicit request.

Pending proposals live in the Django cache (TTL 30 min). A durable
ActionConfirmation record is planned for M2 (T-2012).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from django.core.cache import cache

from ._tools_context import (
    _ctx_explicit_request, _ctx_message, _ctx_phone, _ctx_turn,
)

logger = logging.getLogger(__name__)

PENDING_TTL = 30 * 60

_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|y|ok|okay|confirm(ed)?|sure|go ahead|proceed|do it|"
    r"haan|han|haa|ha|ji|jee|ji haan|theek( hai)?|thik( hai)?|kar do|kardo|"
    r"bilkul|ہاں|جی|ٹھیک)\b",
    re.I,
)
_NEGATE_RE = re.compile(r"\b(no|nope|not|don'?t|cancel|stop|nahi|nahin|mat|na)\b|نہیں", re.I)


def is_affirmative(message: str) -> bool:
    text = (message or '').strip()
    if not text or len(text) > 60:
        return False
    return bool(_AFFIRM_RE.search(text)) and not _NEGATE_RE.search(text)


def _key(phone: str, tool: str) -> str:
    return f"ai_confirm:{phone}:{tool}"


def _fingerprint(params: dict) -> str:
    norm = {k: (str(v).strip().lower() if v is not None else '') for k, v in sorted(params.items())}
    return hashlib.sha256(json.dumps(norm, sort_keys=True).encode()).hexdigest()[:16]


def mark_explicit_request() -> None:
    """Deterministic handlers call this when the user literally asked for the action."""
    _ctx_explicit_request.set(True)


def check(tool: str, params: dict, summary: str, question: str) -> dict | None:
    """
    Return None when the action may run now. Otherwise record a proposal and
    return the tool result that asks the user to confirm.

    ``params`` should hold only the fields that define the action; if they change
    between proposal and confirmation, a fresh confirmation is required.
    """
    if _ctx_explicit_request.get():
        return None

    phone = _ctx_phone.get() or ''
    turn = _ctx_turn.get() or ''
    fp = _fingerprint(params)

    if phone and turn:
        pending = cache.get(_key(phone, tool))
        if (
            pending
            and pending.get('fp') == fp
            and pending.get('turn') != turn
            and is_affirmative(_ctx_message.get())
        ):
            cache.delete(_key(phone, tool))
            logger.info("ai_confirmation accepted tool=%s", tool)
            return None
        cache.set(_key(phone, tool), {'fp': fp, 'turn': turn}, PENDING_TTL)

    return {
        'success': False,
        'confirmation_required': True,
        'summary': summary,
        'message': f"{question}\n\n{summary}\n\nReply *YES* to confirm or *NO* to cancel.",
        'instruction_for_assistant': (
            'Do not claim this action was done. Show the summary and ask the user to '
            'reply YES. Call the tool again with the same details only after they reply.'
        ),
    }
