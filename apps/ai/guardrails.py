"""
Inbound Input Guardrail Engine — RealTron AI (global multi-market edition).

Pipeline position: WhatsApp Webhook → Celery Task → MessageRouter
    → GuardrailEngine.check_input()       ← HERE (pre-LLM gate)
    → GuardrailEngine.check_output()      ← HERE (post-LLM sanity check)

Detection layers (evaluated in order, fastest-to-slowest):
    1. Length guard         O(1)
    2. Redis cache          ~0.5 ms round-trip
    3. NFKC + control strip O(n) — homoglyph defence
    4. Prompt injection     Compiled regex (multilingual)  → CRITICAL
    5. Adversarial override Compiled regex (multilingual)  → HIGH
    6. Jailbreak tricks     Compiled regex                 → HIGH
    7. Profanity            Compiled regex (multilingual)  → MEDIUM
    8. Off-topic            Compiled regex (global topics) → LOW (fail-open w/ RE signal)

Languages covered: en, ar, ur, fr, es, de, tr, hi, ru, zh, pt.

Side effects on HIGH/CRITICAL violations:
    Lead.status → BLOCKED_MALICIOUS | SecurityTrace written | Admin alerted (Celery).

Performance target: ≤300 ms P99 @ 1,000 concurrent requests.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Severity constants
# ──────────────────────────────────────────────────────────────────────────────

class Severity(IntEnum):
    LOW      = 1   # off-topic
    MEDIUM   = 2   # profanity
    HIGH     = 3   # jailbreak / adversarial override
    CRITICAL = 4   # direct prompt injection


# ──────────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    safe:            bool
    reason:          str           = ''
    fallback_reply:  str           = ''
    violation_type:  str           = ''
    severity:        Optional[int] = None
    organization_id: Optional[str] = None
    cached:          bool          = False


@dataclass
class _CachedDecision:
    safe:           bool
    reason:         str
    fallback_reply: str
    violation_type: str
    severity:       Optional[int]


# ──────────────────────────────────────────────────────────────────────────────
# Compiled detection patterns  (module-level → compiled once at import time)
# ──────────────────────────────────────────────────────────────────────────────

# Layer 4 — Direct prompt injection (CRITICAL) — English + 9 other languages
_INJECTION_EN = (
    r'ignore\s+(all\s+)?(previous|prior|above|your)\s+instructions?'
    r'|forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|context|memory)'
    r'|disregard\s+(?:(?:all|the|previous)\s+)?(?:your\s+)?(instructions?|rules?|prompt|guidelines?)'
    r'|new\s+instructions?\s*:'
    r'|override\s+(your\s+)?(instructions?|rules?|system|prompt|behaviour)'
    r'|system\s*:\s*you\s+are'
    r'|<\s*/?(?:system|instructions?|prompt|persona)\s*>'
    r'|\[system\]'
    r'|###\s*(?:system|instructions?|new\s+role)'
    r'|\{\{\s*(?:system|instructions?)\s*\}\}'
)
_INJECTION_MULTI = (
    # Arabic
    r'|تجاهل\s+(?:جميع\s+)?(?:التعليمات|التوجيهات)(?:\s+السابقة)?'
    r'|انسَ?\s+(?:جميع\s+)?التعليمات'
    r'|تعليمات\s+جديدة\s*[:：]'
    r'|تجاوز\s+(?:تعليماتك|القواعد|النظام)'
    # French
    r'|ignorez?\s+(?:toutes?\s+les?\s+)?instructions?\s+précédentes?'
    r'|oubliez?\s+(?:toutes?\s+les?\s+|vos\s+)?instructions?'
    r'|nouvelles?\s+instructions?\s*[:：]'
    # Spanish
    r'|ignora\s+(?:todas?\s+las?\s+)?instrucciones?\s+anteriores?'
    r'|olvida\s+(?:todas?\s+)?(?:las?\s+)?instrucciones?'
    r'|nuevas?\s+instrucciones?\s*[:：]'
    # German
    r'|ignoriere?\s+(?:alle\s+)?(?:vorherigen?\s+)?Anweisungen'
    r'|vergiss\s+(?:alle\s+)?Anweisungen'
    r'|neue\s+Anweisungen\s*[:：]'
    # Turkish
    r'|önceki\s+talimatları\s+(?:yoksay|unut|görmezden\s+gel)'
    r'|yeni\s+talimatlar\s*[:：]'
    # Hindi / Devanagari
    r'|पिछले\s+निर्देशों?\s+को\s+(?:अनदेखा|भूल)\s+करें?'
    r'|नए\s+निर्देश\s*[:：]'
    # Russian
    r'|игнорируй\s+(?:все\s+)?инструкции'
    r'|забудь\s+(?:все\s+)?инструкции'
    r'|новые\s+инструкции\s*[:：]'
    # Chinese Simplified
    r'|忽略(?:之前的|你的|所有)?(?:指令|指示|系统提示)'
    r'|忘记(?:你的|所有)?(?:指令|指示|系统提示)'
    r'|新的?指令\s*[:：]'
    # Portuguese
    r'|ignor[ae]\s+(?:todas?\s+as?\s+)?instruções?\s+anteriores?'
    r'|esqueça?\s+(?:todas?\s+as?\s+)?instruções?'
    r'|novas?\s+instruções?\s*[:：]'
)
_INJECTION_RE = re.compile(f'({_INJECTION_EN}{_INJECTION_MULTI})', re.IGNORECASE)

# Layer 5 — Adversarial role / persona override (HIGH)
_ADVERSARIAL_EN = (
    r'you\s+are\s+now\s+(?!a\s+real\s+estate\s+(?:assistant|AI|chatbot|advisor|expert|analyst)\b)'
    r'|act\s+as\s+(?:if\s+you\s+(?:are|were)|a\s+(?:different|unrestricted|evil|malicious|harmful))'
    r'|pretend\s+(?:you\s+(?:are|were)|to\s+be)\s+(?!interested)'
    r'|your\s+(?:true|real|actual|hidden)\s+(?:purpose|role|identity|instructions?|self)'
    r'|do\s+anything\s+now'
    r'|DAN\s+mode'
    r'|jailbreak'
    r'|developer\s+mode\s+enabled'
    r'|enable\s+(?:developer|unrestricted|god)\s+mode'
    r'|without\s+(?:any\s+)?(?:restrictions?|limitations?|filters?|guidelines?)'
    r'|bypass\s+(?:your\s+)?(?:safety|filter|restriction|guideline|rule)'
    r'|you\s+have\s+no\s+(?:restrictions?|limitations?|rules?)'
    r'|you\s+can\s+(?:say|do|provide)\s+anything'
    r'|in\s+this\s+(?:hypothetical|fictional|alternate|roleplay)\s+(?:scenario|world|universe|reality)'
    r'|roleplay\s+as\s+(?:an?\s+)?(?:evil|unrestricted|unethical|harmful)'
)
_ADVERSARIAL_MULTI = (
    # Arabic
    r'|أنت\s+الآن\s+(?!مساعد?\s+(?:عقار|عقاري))'
    r'|تصرف\s+مثل\s+(?:ذكاء\s+اصطناعي\s+)?(?:غير\s+مقيد|خطير|ضار)'
    r'|بدون\s+(?:أي\s+)?(?:قيود|تعليمات|حدود)'
    # French
    r'|tu\s+es\s+maintenant\s+(?!un\s+assistant\s+immobilier)'
    r'|agis?\s+comme\s+(?:une?\s+)?(?:IA|AI)\s+sans\s+restrictions?'
    r'|sans\s+(?:aucune?\s+)?(?:restrictions?|limitations?|filtres?)'
    # Spanish
    r'|ahora\s+eres?\s+(?!un\s+asistente\s+inmobiliario)'
    r'|actúa\s+como\s+(?:una?\s+)?(?:IA|AI)\s+sin\s+restricciones?'
    # Russian
    r'|ты\s+теперь\s+(?!помощник\s+по\s+недвижимости)'
    r'|действуй\s+как\s+(?:злобный|неограниченный|вредоносный)\s+(?:ИИ|AI)'
    r'|без\s+(?:каких-либо\s+)?(?:ограничений|правил|инструкций)'
    # Chinese
    r'|你现在是(?!一个?房地产助手)'
    r'|扮演(?:一个?)?(?:邪恶的|不受限制的|有害的)(?:AI|人工智能)'
    r'|没有任何?(?:限制|规则|指令)'
)
_ADVERSARIAL_RE = re.compile(f'({_ADVERSARIAL_EN}{_ADVERSARIAL_MULTI})', re.IGNORECASE)

# Layer 6 — Jailbreak encoding / token tricks (HIGH)
_JAILBREAK_RE = re.compile(
    r'('
    r'base64\s+(?:decode|encoded|payload)'
    r'|rot13'
    r'|caesar\s+cipher'
    r'|translate\s+(?:this|the\s+following)\s+from\s+(?:base64|rot13|cipher)'
    r'|\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2})+'
    r'|repeat\s+after\s+me\s*:'
    r'|say\s+the\s+(?:word|phrase)\s+[""](?:ignore|forget|disregard|override)'
    r'|complete\s+this\s+(?:sentence|text)\s*:\s*ignore'
    r'|fill\s+in\s+the\s+blank.{0,20}ignore'
    r')',
    re.IGNORECASE,
)

# Layer 7 — Profanity (MEDIUM) — multilingual; override via GUARDRAIL_PROFANITY_PATTERNS
_DEFAULT_PROFANITY_PATTERN = (
    r'(?:'
    # English
    r'\bb+u+l+l+s+h+i+t+\b'
    r'|m+o+t+h+e+r+f+u+c+k+e+r+\b'
    r'|\b(?:f+u+c+k+(?:ing|er|ed|s)?|s+h+i+t+(?:ty|ter|s)?'
    r'|b+i+t+c+h+(?:es|ing)?|a+s+s+h+o+l+e+)\b'
    # Urdu / Pakistani
    r'|\b(?:bhenchod|madarchod|chutiya|harami|gaandu|randi)\b'
    # Arabic
    r'|\bشرموطة\b|\bقحبة\b'
    r'|يلعن\s+(?:أبوك|أمك)'
    # French
    r'|\bputain\b|\bmerde\b|\bconnard\b|\benculé\b'
    # Spanish
    r'|hijo\s+de\s+puta|\bcoño\b|\bjoder\b|\bcabrón\b'
    # German
    r'|\bscheiße?\b|\bfick\s+dich\b|\barschloch\b'
    # Turkish
    r'|\borospu\s+çocuğu\b|\bsiktir\b'
    # Russian
    r'|\bблядь\b|\bпизда\b|\bхуй\b|\bсука\b'
    # Chinese
    r'|傻逼|操你妈|妈的(?!还好)'
    r')'
)

# Layer 8 — Off-topic (LOW) — global keywords; fail-open when RE signal present
_OFF_TOPIC: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r'\b(politics|election|president|prime[\s-]?minister|parliament|congress|'
        r'senate|انتخابات|برلمان|élection|parlement|elección|parlamento|'
        r'Wahl|Parlament|seçim|meclis)\b',
        re.I | re.UNICODE),
     'politics'),
    (re.compile(
        r'\b(diagnos|symptom|prescription|medicine|tablet|injection|surgery|'
        r'hospital|clinic|doctor|disease|cancer|diabetes|blood[\s-]?pressure)\b', re.I),
     'medical advice'),
    (re.compile(
        r'\b(forex|crypto|bitcoin|ethereum|NFT|stock[\s-]?market|trading[\s-]?bot|'
        r'investment[\s-]?tip|pump[\s-]?and[\s-]?dump|coin|altcoin|blockchain)\b', re.I),
     'crypto/financial speculation'),
    (re.compile(r'\b(recipe|cooking|food|restaurant|diet|calories|nutrition)\b', re.I),
     'food / cooking'),
    (re.compile(r'\b(flight|hotel[\s-]?booking|travel|visa|passport|airline|tourism)\b', re.I),
     'travel / tourism'),
]

# Real-estate signal — multilingual property keywords; presence overrides off-topic block
_RE_ESTATE_SIGNAL = re.compile(
    r'\b(?:'
    # English / Pakistani Latin
    r'property|plot|house|home|flat|apartment|makaan|ghar|zameen|'
    r'marla|kanal|sqft|square[\s-]?feet|'
    r'DHA|Bahria|Gulberg|Defence|Clifton|PECHS|'
    r'rent|buy|sell|invest|lease|'
    r'mortgage|loan|EMI|down[\s-]?payment|'
    r'tax|filer|7E|CGT|stamp[\s-]?duty|'
    r'agent|broker|developer|society|'
    r'fard|registry|allotment|NOC|'
    r'fraud|scam|fake|verify|check[\s-]?deal|deal[\s-]?check'
    r')\b'
    # Arabic
    r'|عقار|شقة|فيلا|أرض|إيجار|شراء|بيع|استثمار'
    # Urdu script
    r'|مکان|فلیٹ|زمین|کرایہ|خریدنا|فروخت'
    # Hindi / Devanagari
    r'|मकान|फ्लैट|किराया|खरीदना|बेचना|संपत्ति'
    # Russian
    r'|недвижимость|квартира|аренда|купить|продать'
    # Chinese Simplified
    r'|房产|公寓|租房|买房|卖房|楼盘'
    # French
    r'|immobilier|appartement|louer|acheter|investissement'
    # Spanish
    r'|inmueble|apartamento|alquilar|comprar|inversión'
    # German
    r'|Immobilien|Wohnung|mieten|kaufen|Investition'
    # Turkish
    r'|emlak|daire|kira|yatırım'
    # Portuguese
    r'|imóvel|alugar|investimento',
    re.I | re.UNICODE,
)

_MIN_LEN          = 2
_MAX_LEN          = 3000
_FAILURE_LITERALS = frozenset({'none', 'null', 'error', 'n/a', 'undefined', ''})

_CACHE_PREFIX = 'guardrail:v1:'
_CACHE_TTL    = 300  # seconds


# ──────────────────────────────────────────────────────────────────────────────
# Reply templates — multilingual (keyed by ISO-639-1 code; 'en' is fallback)
# ──────────────────────────────────────────────────────────────────────────────

_SECURITY_REPLIES: dict[str, str] = {
    'ur': "یہ پیغام پروسیس نہیں کیا جا سکتا۔\nبراہ کرم property search، tax advice، یا scam check کے بارے میں پوچھیں۔",
    'ar': "لا يمكنني معالجة هذه الرسالة.\nيرجى السؤال عن البحث عن العقارات أو الاستشارات الضريبية أو التحقق من الصفقات.",
    'fr': "Je ne peux pas traiter ce message.\nVeuillez poser des questions sur la recherche immobilière, les conseils fiscaux ou la vérification de transactions.",
    'es': "No puedo procesar ese mensaje.\nPor favor, consulta sobre búsqueda de propiedades, asesoría fiscal o verificación de tratos.",
    'de': "Diese Nachricht kann ich nicht verarbeiten.\nBitte fragen Sie nach Immobiliensuche, Steuerberatung oder Betrugsprüfungen.",
    'tr': "Bu mesajı işleyemiyorum.\nLütfen mülk arama, vergi danışmanlığı veya dolandırıcılık kontrolü hakkında sorun.",
    'hi': "मैं यह संदेश संसाधित नहीं कर सकता।\nकृपया संपत्ति खोज, कर सलाह या धोखाधड़ी जाँच के बारे में पूछें।",
    'ru': "Я не могу обработать это сообщение.\nПожалуйста, задайте вопрос о поиске недвижимости, налоговых советах или проверке сделок.",
    'zh': "我无法处理这条消息。\n请询问有关房产搜索、税务建议或交易核实的问题。",
    'pt': "Não consigo processar essa mensagem.\nPor favor, pergunte sobre pesquisa de imóveis, aconselhamento fiscal ou verificação de negócios.",
}

_PROFANITY_REPLIES: dict[str, str] = {
    'ur': "Meherbani farma kar achi zaban use karein.\nMain property, tax, ya kisi aur real estate sawaal mein madad kar sakta hoon.",
    'ar': "يرجى الحفاظ على أسلوب محترم في المحادثة.\nأنا هنا للمساعدة في العقارات والخدمات المرتبطة بها.",
    'fr': "Merci de garder un ton respectueux.\nJe suis là pour vous aider dans vos démarches immobilières.",
    'es': "Por favor, mantén un tono respetuoso.\nEstoy aquí para ayudarte con tus consultas inmobiliarias.",
    'de': "Bitte bleiben Sie respektvoll.\nIch helfe Ihnen gerne bei Immobilienfragen weiter.",
    'tr': "Lütfen saygılı bir dil kullanın.\nSize emlak konularında yardımcı olmaktan memnuniyet duyarım.",
    'ru': "Пожалуйста, соблюдайте уважительный тон.\nЯ готов помочь вам с вопросами недвижимости.",
    'zh': "请保持礼貌的对话。\n我可以帮您解答房产相关问题。",
    'pt': "Por favor, mantenha um tom respeitoso.\nEstou aqui para ajudar com consultas imobiliárias.",
}

_OFF_TOPIC_REPLIES: dict[str, str] = {
    'ur': "Main sirf real estate mein madad karta hoon — property search, tax advice, fraud check, loan, aur deal management.\n\nKya aap property ke baare mein kuch poochna chahte hain? 🏠",
    'ar': "أستطيع المساعدة في مجال العقارات فقط — البحث عن العقارات، النصائح الضريبية، التحقق من الاحتيال، وإدارة الصفقات.\n\nهل لديك سؤال عقاري؟ 🏠",
    'fr': "Je ne peux vous aider qu'en matière immobilière — recherche, fiscalité, anti-fraude et gestion des transactions.\n\nAvez-vous une question immobilière ? 🏠",
    'es': "Solo puedo ayudarte con temas inmobiliarios — búsqueda de propiedades, asesoría fiscal, detección de fraudes y gestión de transacciones.\n\n¿Tienes alguna consulta sobre propiedades? 🏠",
    'de': "Ich kann nur bei Immobilienthemen helfen — Suche, Steuern, Betrugsprüfung und Transaktionsmanagement.\n\nHaben Sie eine Immobilienfrage? 🏠",
    'tr': "Yalnızca gayrimenkul konularında yardımcı olabilirim — mülk arama, vergi tavsiyesi, dolandırıcılık kontrolü ve işlem yönetimi.\n\nGayrimenkulle ilgili bir sorunuz var mı? 🏠",
    'ru': "Я могу помочь только по вопросам недвижимости — поиск объектов, налоговые советы, проверка мошенничества и управление сделками.\n\nЕсть вопрос о недвижимости? 🏠",
    'zh': "我只能提供房地产相关帮助——房产搜索、税务建议、防诈骗和交易管理。\n\n有关于房产的问题吗？ 🏠",
    'pt': "Só posso ajudar com imóveis — pesquisa de propriedades, aconselhamento fiscal, detecção de fraudes e gestão de negócios.\n\nTem alguma questão imobiliária? 🏠",
}

_OFF_TOPIC_EN = (
    "I can only help with real estate — property search, tax advice, "
    "fraud/scam checks, loan eligibility, and deal management.\n\n"
    "Do you have a property-related question? 🏠"
)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level pattern cache (lazy-loaded once per process)
# ──────────────────────────────────────────────────────────────────────────────

_profanity_re: Optional[re.Pattern] = None


def _get_profanity_re() -> re.Pattern:
    global _profanity_re
    if _profanity_re is None:
        try:
            from django.conf import settings
            custom = getattr(settings, 'GUARDRAIL_PROFANITY_PATTERNS', None)
            if custom and isinstance(custom, str):
                _profanity_re = re.compile(custom, re.IGNORECASE | re.UNICODE)
                return _profanity_re
        except Exception:
            pass
        _profanity_re = re.compile(_DEFAULT_PROFANITY_PATTERN, re.IGNORECASE | re.UNICODE)
    return _profanity_re


# ──────────────────────────────────────────────────────────────────────────────
# GuardrailEngine
# ──────────────────────────────────────────────────────────────────────────────

class GuardrailEngine:
    """
    Stateless, thread-safe input/output guardrail.
    Module-level compiled patterns are read-only after import — safe under
    any number of concurrent threads/workers.
    """

    @classmethod
    def check_input(
        cls,
        message:         str,
        language:        str            = 'en',
        organization_id: Optional[str]  = None,
        phone:           Optional[str]  = None,
    ) -> GuardrailResult:
        """
        Pre-LLM gate.  Call before every AI invocation.

        Returns GuardrailResult(safe=True)  → proceed to LLM.
        Returns GuardrailResult(safe=False) → return fallback_reply to user.

        Side effects on HIGH/CRITICAL violations (fire-and-forget, never raises):
            Lead.status → BLOCKED_MALICIOUS | SecurityTrace written | Admin alerted.
        """
        t0  = time.monotonic()
        msg = message.strip()

        # L1: length guard (O(1), no cache)
        if len(msg) < _MIN_LEN:
            return GuardrailResult(
                safe=False, reason='too_short', violation_type='length',
                fallback_reply="Please type your question or property request.",
            )
        if len(msg) > _MAX_LEN:
            return GuardrailResult(
                safe=False, reason='too_long', violation_type='length',
                fallback_reply=(
                    "Your message is very long. "
                    "Please keep it under 3,000 characters and I'll be happy to help."
                ),
            )

        # L2: Redis cache hit
        cache_key = _CACHE_PREFIX + _msg_hash(msg)
        cached    = _cache_get(cache_key)
        if cached is not None:
            result = GuardrailResult(
                safe=cached.safe,
                reason=cached.reason,
                fallback_reply=cached.fallback_reply,
                violation_type=cached.violation_type,
                severity=cached.severity,
                organization_id=organization_id,
                cached=True,
            )
            if not cached.safe and cached.severity and cached.severity >= Severity.HIGH:
                cls._run_side_effects(msg, phone, organization_id,
                                      cached.violation_type, cached.severity)
            return result

        # L3: NFKC normalization (homoglyph defence) + control-char strip
        msg = _strip_control(msg)

        # L4: critical prompt injection
        if _INJECTION_RE.search(msg):
            return cls._block(
                msg, cache_key, phone, organization_id, language,
                violation_type='injection',
                severity=Severity.CRITICAL,
                reason='prompt_injection',
                reply=_security_reply(language),
            )

        # L5: adversarial role / persona override
        if _ADVERSARIAL_RE.search(msg):
            return cls._block(
                msg, cache_key, phone, organization_id, language,
                violation_type='adversarial',
                severity=Severity.HIGH,
                reason='adversarial_override',
                reply=_security_reply(language),
            )

        # L6: jailbreak encoding tricks
        if _JAILBREAK_RE.search(msg):
            return cls._block(
                msg, cache_key, phone, organization_id, language,
                violation_type='jailbreak',
                severity=Severity.HIGH,
                reason='jailbreak_attempt',
                reply=_security_reply(language),
            )

        # L7: profanity (MEDIUM — warn, don't permanently block lead)
        if _get_profanity_re().search(msg):
            result = GuardrailResult(
                safe=False,
                reason='profanity',
                violation_type='profanity',
                severity=int(Severity.MEDIUM),
                organization_id=organization_id,
                fallback_reply=_profanity_reply(language),
            )
            _cache_set(cache_key, result)
            _write_security_trace(msg, phone, organization_id, 'profanity', Severity.MEDIUM)
            return result

        # L8: off-topic (only when no real-estate signal present)
        if not _RE_ESTATE_SIGNAL.search(msg):
            for pattern, topic in _OFF_TOPIC:
                if pattern.search(msg):
                    result = GuardrailResult(
                        safe=False,
                        reason=f'off_topic:{topic}',
                        violation_type='off_topic',
                        severity=int(Severity.LOW),
                        organization_id=organization_id,
                        fallback_reply=_off_topic_reply(topic, language),
                    )
                    _cache_set(cache_key, result)
                    logger.info("Guardrail off-topic blocked: %s phone=%s", topic, phone)
                    return result

        safe_result = GuardrailResult(safe=True)
        _cache_set(cache_key, safe_result)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > 50:
            logger.warning("GuardrailEngine.check_input slow: %.1fms phone=%s", elapsed_ms, phone)
        return safe_result

    @staticmethod
    def check_output(reply: str) -> bool:
        """Post-LLM sanity check. Returns False for empty / failure literals / tracebacks."""
        if not isinstance(reply, str):
            return False
        stripped = reply.strip()
        if len(stripped) < 5:
            return False
        if stripped.lower() in _FAILURE_LITERALS:
            return False
        if 'Traceback (most recent call last)' in reply:
            return False
        return True

    @staticmethod
    def fallback_error_reply(language: str = 'en') -> str:
        replies = {
            'ur': "Maafi, kuch masla aa gaya. Dobara koshish karein.\n*menu* type karein options dekhne ke liye.",
            'ar': "عذراً، حدث خطأ. يرجى المحاولة مرة أخرى.\nاكتب *menu* للاطلاع على الخيارات.",
            'fr': "Une erreur s'est produite. Veuillez réessayer.\nTapez *menu* pour voir les options disponibles.",
            'es': "Ocurrió un error. Por favor, inténtalo de nuevo.\nEscribe *menu* para ver las opciones.",
        }
        return replies.get(
            language,
            "Something went wrong on my end. Please try again in a moment.\n"
            "Type *menu* to see what I can help you with."
        )

    @classmethod
    def _block(
        cls,
        msg:             str,
        cache_key:       str,
        phone:           Optional[str],
        organization_id: Optional[str],
        language:        str,
        *,
        violation_type:  str,
        severity:        Severity,
        reason:          str,
        reply:           str,
    ) -> GuardrailResult:
        result = GuardrailResult(
            safe=False,
            reason=reason,
            violation_type=violation_type,
            severity=int(severity),
            organization_id=organization_id,
            fallback_reply=reply,
        )
        _cache_set(cache_key, result)
        cls._run_side_effects(msg, phone, organization_id, violation_type, int(severity))
        return result

    @classmethod
    def _run_side_effects(
        cls,
        msg:             str,
        phone:           Optional[str],
        organization_id: Optional[str],
        violation_type:  str,
        severity:        int,
    ) -> None:
        """Fire-and-forget — must never raise."""
        try:
            _write_security_trace(msg, phone, organization_id, violation_type, severity)
        except Exception as exc:
            logger.error("Guardrail: security trace write failed: %s", exc)

        if severity >= Severity.HIGH:
            try:
                _block_lead(phone, organization_id)
            except Exception as exc:
                logger.error("Guardrail: lead block failed: %s", exc)
            try:
                _alert_admins_async(phone, organization_id, violation_type)
            except Exception as exc:
                logger.error("Guardrail: admin alert dispatch failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helper functions (kept module-level so tests can patch cleanly)
# ──────────────────────────────────────────────────────────────────────────────

def _msg_hash(text: str) -> str:
    return hashlib.sha256(text[:500].encode('utf-8', errors='replace')).hexdigest()[:32]


def _strip_control(text: str) -> str:
    """NFKC-normalize (collapse homoglyphs), then remove null bytes / invisible Unicode."""
    text = unicodedata.normalize('NFKC', text)
    return ''.join(
        ch for ch in text
        if ch in ('\n', '\t') or not unicodedata.category(ch).startswith('C')
    )


def _cache_get(key: str) -> Optional[_CachedDecision]:
    try:
        from django.core.cache import cache
        raw = cache.get(key)
        if raw is None:
            return None
        d = json.loads(raw)
        return _CachedDecision(
            safe=d['safe'], reason=d['reason'],
            fallback_reply=d['fallback_reply'],
            violation_type=d['violation_type'],
            severity=d.get('severity'),
        )
    except Exception:
        return None


def _cache_set(key: str, result: GuardrailResult) -> None:
    try:
        from django.core.cache import cache
        payload = json.dumps({
            'safe': result.safe, 'reason': result.reason,
            'fallback_reply': result.fallback_reply,
            'violation_type': result.violation_type,
            'severity': result.severity,
        })
        cache.set(key, payload, _CACHE_TTL)
    except Exception:
        pass


def _block_lead(phone: Optional[str], organization_id: Optional[str]) -> None:
    if not phone:
        return
    try:
        from django.contrib.auth import get_user_model
        from apps.leads.models import Lead
        User = get_user_model()
        normalized = f"+{phone.lstrip('+')}"
        user = User.objects.filter(phone=normalized).first()
        if not user:
            return
        qs = Lead.objects.filter(user=user)
        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        updated = qs.update(status=Lead.Status.BLOCKED_MALICIOUS)
        logger.warning(
            "Guardrail: stamped %d lead(s) BLOCKED_MALICIOUS phone=%s org=%s",
            updated, phone, organization_id,
        )
    except Exception as exc:
        logger.error("Guardrail _block_lead: %s", exc)


def _write_security_trace(
    msg:             str,
    phone:           Optional[str],
    organization_id: Optional[str],
    violation_type:  str,
    severity:        int,
) -> None:
    try:
        from apps.audit.models import SecurityTrace
        SecurityTrace.objects.create(
            phone=phone or '',
            organization_id=str(organization_id) if organization_id else '',
            violation_type=violation_type,
            severity=severity,
            message_excerpt=msg[:300],
        )
    except Exception as exc:
        logger.warning("SecurityTrace DB write failed (%s) — emitting structured log", exc)
        logger.error(
            "SECURITY_TRACE violation=%s severity=%d org=%s phone=%s excerpt=%r",
            violation_type, severity, organization_id, phone, msg[:100],
        )


def _alert_admins_async(
    phone:           Optional[str],
    organization_id: Optional[str],
    violation_type:  str,
) -> None:
    try:
        from apps.ai.tasks import alert_security_violation
        alert_security_violation.delay(
            phone=phone or '',
            organization_id=str(organization_id) if organization_id else '',
            violation_type=violation_type,
        )
    except Exception as exc:
        logger.warning("Guardrail async alert failed (%s) — attempting sync fallback", exc)
        try:
            from django.contrib.auth import get_user_model
            from apps.notifications.services import notify_user
            User = get_user_model()
            title   = f"\U0001f6a8 Security Alert: {violation_type.replace('_', ' ').title()}"
            message = (
                f"\U0001f6a8 *Guardrail blocked a {violation_type} attempt.*\n\n"
                f"Phone: `{phone or 'unknown'}`  |  Org: `{organization_id or 'none'}`"
            )
            for admin in User.objects.filter(role='admin', is_active=True)[:5]:
                notify_user(admin, title=title, message=message)
        except Exception as inner:
            logger.error("Guardrail admin notify sync fallback failed: %s", inner)


# ── Reply helpers ──────────────────────────────────────────────────────────────

def _security_reply(language: str) -> str:
    return _SECURITY_REPLIES.get(
        language,
        "I can't process that message. "
        "Please ask about property search, tax advice, scam checks, or deal management."
    )


def _profanity_reply(language: str) -> str:
    return _PROFANITY_REPLIES.get(
        language,
        "Please keep the conversation respectful.\n"
        "I'm here to help with property search, tax advice, and real estate services."
    )


def _off_topic_reply(topic: str, language: str) -> str:
    return _OFF_TOPIC_REPLIES.get(language, _OFF_TOPIC_EN)
