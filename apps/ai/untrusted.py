"""
Text written by other people, on its way to a model (T-2071, ADR-044, AI spec §8.1).

Listing titles and descriptions, place and agent names, chat transcripts and
captions are data. Before a model sees them:

* sentences that give the assistant orders (attack wording the input guard knows,
  or wording addressed to an assistant) are replaced by ``[removed]``;
* ``<`` and ``>`` are replaced, so the text cannot close or open a delimiter;
* long free text is wrapped in ``<lister_text>``.

Only the model's copy changes. Stored text, cards and the grounding check use the
original. This lowers the risk; confirmations, tool policy and grounding are what
hold if a new wording gets through.
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

REMOVED = '[removed]'
_WRAPPED = {'description'}                                        # free text shown as a block
_PLATFORM = {'instruction', '_instruction', 'message', 'error'}   # top-level keys written by our tools

_WHO = r'(?:ai|a\.i\.|assistant|chat\s?bot|bot|language\s+model|llm|gpt|gemini|claude)'
_DIRECTED = re.compile(
    rf'^\W*(?:(?:dear|hey|hello|attention)\s+)?{_WHO}(?:\s+(?:assistant|model|bot|agent))?\s*[:,]'
    rf'|\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:the\s+|any\s+|all\s+)?{_WHO}\b'
    rf'|\b(?:if|when|since)\s+you\s+are\s+(?:an?\s+|the\s+)?{_WHO}\b'
    rf'|\b{_WHO}s?\s+(?:must|should|shall|reading\s+this)\b'
    r'|\b(?:tell|inform|instruct|convince|advise)\s+(?:the\s+|every\s+|all\s+|any\s+)?(?:users?|buyers?|customers?|clients?)\s+'
    r'(?:that|to|this|it)\b'
    r'|\b(?:call|use|invoke|run|execute)\s+the\s+\w+\s+(?:tool|function)\b'
    r'|\b(?:do\s+not|don\'?t|never)\s+(?:mention|show|recommend|suggest)\s+(?:any\s+)?other\s+(?:listings?|properties)\b'
    r'|\balways\s+(?:recommend|rank|show|suggest)\s+this\s+(?:listing|property)\b'
    r'|\b(?:say|state|claim|answer|reply)\s+(?:that\s+)?(?:this|the)\s+(?:listing|property|title|price)\s+is\b'
    r'|\bmark\s+this\s+(?:listing|property)\s+as\b'
    # Roman Urdu, Urdu
    r'|\b(?:user|customer|client|kh?ar[ei]+da+r|gahak)\s+ko\s+(?:bata|bol|kah|keh)\w*'
    r'|\b(?:ai|assistant|bot)\s+(?:ko|ke\s+liye)\s+(?:hidaya+t|hukm)\b'
    r'|(?:صارف|خریدار|گاہک|کسٹمر)\s+کو\s+(?:بتا|کہ)'
    r'|(?:اے\s*آئی|اسسٹنٹ|بوٹ)\s+(?:کو|کے\s+لیے)\s+(?:ہدایت|حکم)', re.I)
_SENTENCE = re.compile(r'(?<=[.!?؟۔])\s+|\n+')
_TOOLS = None


def _tool_names() -> re.Pattern:
    global _TOOLS
    if _TOOLS is None:
        from .tool_registry import REGISTRY
        _TOOLS = re.compile(r'\b(?:' + '|'.join(map(re.escape, REGISTRY)) + r')\b', re.I)
    return _TOOLS


def _clean(text: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFKC', text)
                   if ch in '\n\t' or ch in '\u200c\u200d' or not unicodedata.category(ch).startswith('C'))


def suspicious(text: str) -> bool:
    """Does the text give orders to an assistant?"""
    from .guardrails import GuardrailEngine, Severity
    text = re.sub('[\u200c\u200d]', '', _clean(text or ''))
    flat = ' '.join(text.split())                  # an order broken over lines
    return bool(any((GuardrailEngine.inspect(t)[2] or 0) >= Severity.HIGH for t in (text, flat))
                or _tool_names().search(text)
                or any(_DIRECTED.search(s) for s in (flat, *_SENTENCE.split(text))))


def neutralise(text: str) -> str:
    if not text or not text.strip():
        return text
    out = _clean(text)
    if suspicious(out):
        parts = re.split(f'({_SENTENCE.pattern})', out)
        kept = ''.join(REMOVED if p.strip() and suspicious(p) else p for p in parts)
        # An order spread over several sentences or lines: nothing of it is safe to keep.
        out = REMOVED if suspicious(kept) else kept
        _note()
    return out.replace('<', '‹').replace('>', '›')


def _note():
    from . import tracing
    logger.warning('ai.untrusted: instructions removed from third-party text')
    tracing.note_guard('tool_text', 'indirect_injection')


def for_model(result):
    """The model's copy of a tool result."""
    return _walk(result, top=True)


def _walk(value, key='', top=False):
    if isinstance(value, dict):
        return {k: v if top and k in _PLATFORM else _walk(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk(v, key) for v in value]
    if isinstance(value, str):
        text = neutralise(value)
        return f'<lister_text>{text}</lister_text>' if key in _WRAPPED and text else text
    return value


def line(text) -> str:
    """One neutralised line, for names placed in a system prompt."""
    return ' '.join(neutralise(str(text or '')).split())[:200]


# Prompt variables that carry people's own text (apps.ai.llm.render).
PROMPT_VARIABLES = frozenset({
    'title', 'location', 'city', 'area', 'listings', 'query', 'caption', 'transcript', 'recent', 'last_inbound',
    'crm_timeline', 'intent', 'org_name'})


def prompt_values(values: dict) -> dict:
    return {k: neutralise(v) if k in PROMPT_VARIABLES and isinstance(v, str) else v for k, v in values.items()}
