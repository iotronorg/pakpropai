"""
Pre- and post-LLM guardrails for RealTron AI.

Two checkpoints:
  check_input()  — before the LLM call: off-topic detection, length limits
  check_output() — after the LLM call: validates the response is usable

Design principle: guardrails should fail OPEN for ambiguous messages
(i.e. let the LLM handle them) and fail CLOSED only for clear violations.
A message about medicine that also mentions "property" passes through.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    safe:           bool
    reason:         str  = ''
    fallback_reply: str  = ''


# ── Off-topic pattern definitions ─────────────────────────────────────────────
# Each entry: (compiled regex, human-readable topic label)
# Triggers only when the message does NOT also contain real-estate signals.

_OFF_TOPIC: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r'\b(politics|election|prime.?minister|chief.?minister|army.?chief|COAS|'
            r'PTI|PMLN|PPP|parliament|judiciary|court.?order|supreme.?court)\b',
            re.I,
        ),
        'politics',
    ),
    (
        re.compile(
            r'\b(diagnos|symptom|prescription|medicine|tablet|injection|surgery|'
            r'hospital|clinic|doctor|disease|cancer|diabetes|blood.?pressure)\b',
            re.I,
        ),
        'medical advice',
    ),
    (
        re.compile(
            r'\b(forex|crypto|bitcoin|ethereum|NFT|stock.?market|trading.?bot|'
            r'investment.?tip|pump.?and.?dump|coin)\b',
            re.I,
        ),
        'non-real-estate financial speculation',
    ),
    (
        re.compile(r'\b(recipe|cooking|food|restaurant|diet|calories|nutrition)\b', re.I),
        'food / cooking',
    ),
    (
        re.compile(
            r'\b(flight|hotel.?booking|travel|visa|passport|airline|tourism)\b',
            re.I,
        ),
        'travel / tourism',
    ),
]

# Any message containing one of these keywords is presumed real-estate-related
# and will pass through even if an off-topic pattern fires.
_RE_ESTATE_SIGNAL = re.compile(
    r'\b(property|plot|house|home|flat|apartment|makaan|ghar|zameen|'
    r'marla|kanal|sqft|square.?feet|'
    r'DHA|Bahria|Gulberg|Defence|Clifton|PECHS|'
    r'rent|buy|sell|invest|lease|'
    r'mortgage|loan|EMI|down.?payment|'
    r'tax|filer|7E|CGT|stamp.?duty|'
    r'agent|broker|developer|society|'
    r'fard|registry|allotment|NOC|'
    r'fraud|scam|fake|verify|check.?deal|deal.?check)\b',
    re.I,
)

_MIN_LEN =    2
_MAX_LEN = 3000

# Strings the model sometimes returns on hard failure — treat as unusable output
_FAILURE_LITERALS = {'none', 'null', 'error', 'n/a', 'undefined', ''}


class GuardrailEngine:

    @classmethod
    def check_input(cls, message: str, language: str = 'en') -> GuardrailResult:
        """
        Run input safety checks before sending to the LLM.
        Returns GuardrailResult(safe=True) to proceed, or safe=False with a
        fallback_reply to return directly to the user.
        """
        msg = message.strip()

        if len(msg) < _MIN_LEN:
            return GuardrailResult(
                safe=False,
                reason='too_short',
                fallback_reply="Please type your question or property request.",
            )

        if len(msg) > _MAX_LEN:
            return GuardrailResult(
                safe=False,
                reason='too_long',
                fallback_reply=(
                    "Your message is very long. "
                    "Please keep it under 3,000 characters and I'll be happy to help."
                ),
            )

        # Off-topic detection — only block when no real-estate keyword is present
        if not _RE_ESTATE_SIGNAL.search(msg):
            for pattern, topic in _OFF_TOPIC:
                if pattern.search(msg):
                    logger.info("Guardrail blocked off-topic input: %s", topic)
                    return GuardrailResult(
                        safe=False,
                        reason=f'off_topic:{topic}',
                        fallback_reply=cls._off_topic_reply(topic, language),
                    )

        return GuardrailResult(safe=True)

    @staticmethod
    def check_output(reply: str) -> bool:
        """
        Returns True if the LLM output is usable.
        Returns False for empty, whitespace-only, or obvious failure strings.
        """
        if not isinstance(reply, str):
            return False
        stripped = reply.strip()
        if len(stripped) < 5:
            return False
        if stripped.lower() in _FAILURE_LITERALS:
            return False
        # Detect raw exception traces that should never reach a user
        if 'Traceback (most recent call last)' in reply:
            return False
        return True

    # ── Fallback templates ─────────────────────────────────────────────────────

    @staticmethod
    def _off_topic_reply(topic: str, language: str) -> str:
        if language == 'ur':
            return (
                "Main sirf real estate mein madad karta hoon — property search, "
                "tax advice, fraud check, loan eligibility, aur deal management.\n\n"
                "Kya aap property ke baare mein kuch poochna chahte hain? 🏠"
            )
        return (
            "I can only help with real estate — property search, tax advice, "
            "fraud/scam checks, loan eligibility, and deal management.\n\n"
            "Do you have a property-related question? 🏠"
        )

    @staticmethod
    def fallback_error_reply(language: str = 'en') -> str:
        """Standard reply when the LLM output fails check_output()."""
        if language == 'ur':
            return (
                "Maafi, kuch masla aa gaya. Dobara koshish karein.\n"
                "*menu* type karein options dekhne ke liye."
            )
        return (
            "Something went wrong on my end. Please try again in a moment.\n"
            "Type *menu* to see what I can help you with."
        )
