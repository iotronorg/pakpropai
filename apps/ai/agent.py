"""
RealTron AI Agent — the core intelligence of the platform.

Architecture:
- Backend-agnostic: uses AIBackend abstraction (Gemini or Ollama)
- Conversation history persisted in Redis (24h TTL, last 20 turns)
- Real estate domain knowledge embedded in system prompt
- Switch backend via AI_BACKEND env var: 'gemini' | 'local'
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache

from apps.ai.backends import get_backend

logger = logging.getLogger(__name__)

HISTORY_KEY = 'ai:conv:{phone}'
HISTORY_TTL = 86400       # 24 hours
MAX_TURNS   = 20          # keep last 20 user+model turns (40 entries)


class RealTronAgent:

    def __init__(self):
        self._backend = None

    def _get_backend(self):
        if self._backend is None:
            self._backend = get_backend()
            logger.info(f"RealTronAgent using backend: {self._backend.label}")
        return self._backend

    # ─── Public interface ─────────────────────────────────────────────────────

    def _get_tools(self, tool_module) -> list:
        """Returns the active tool list based on admin feature flags."""
        from apps.config.services import SystemConfigService
        features = SystemConfigService.get_features()
        mapping = [
            ('feature_property_search',    tool_module.search_properties),
            ('feature_property_listing',   tool_module.list_property),
            ('feature_scam_check',         tool_module.run_fraud_check),
            ('feature_tax_advice',         tool_module.calculate_7e_tax),
            ('feature_loan_eligibility',   tool_module.check_loan_eligibility),
            ('feature_talk_to_agent',      tool_module.connect_to_agent),
            ('feature_deal_lock',          tool_module.initiate_deal_lock),
            ('feature_property_audit',     tool_module.generate_property_audit),
        ]
        return [fn for flag, fn in mapping if features.get(flag, True)]

    def chat(self, phone: str, message: str, user=None) -> str:
        """Process a WhatsApp text message. Returns the agent's reply."""
        backend = self._get_backend()

        # Gemini requires an API key; local (Ollama) does not
        if backend.label.startswith('gemini'):
            from apps.config.services import SystemConfigService
            if not SystemConfigService.get('gemini_api_key'):
                return (
                    "AI service is not configured yet.\n"
                    "Please ask the admin to set the Gemini API key in System Setup.\n\n"
                    "Get a free key at: aistudio.google.com"
                )

        from apps.ai import tools as tool_module

        tool_module.set_context(user, phone)

        # Greetings get an instant structured reply — no model call needed
        if self._is_greeting(message):
            reply = self._greeting_reply(message)
            history = self._load_history(phone)
            self._save_history(phone, history, message, reply)
            return reply

        # Agent requests are handled directly in Python — local models hallucinate
        # agent details when this is left to the model, so we bypass it entirely.
        if self._is_agent_request(message):
            history = self._load_history(phone)
            reply = self._handle_agent_request(message, history, tool_module)
            self._save_history(phone, history, message, reply)
            return reply

        from apps.ai.knowledge import SYSTEM_PROMPT

        history = self._load_history(phone)
        start   = time.time()
        tools   = self._get_tools(tool_module)

        try:
            reply = backend.chat(message, history, tools, SYSTEM_PROMPT)
        except Exception as exc:
            logger.error(f"Backend chat failed phone={phone}: {exc}", exc_info=True)
            reply = self._error_reply()

        self._save_history(phone, history, message, reply)
        self._log(user, message, reply, int((time.time() - start) * 1000))
        return reply

    def chat_with_image(self, phone: str, image_bytes: bytes, mime_type: str,
                        caption: str = '', user=None) -> str:
        """Process an image/document message through the agent."""
        backend = self._get_backend()

        if backend.label.startswith('gemini'):
            from apps.config.services import SystemConfigService
            if not SystemConfigService.get('gemini_api_key'):
                return "AI service not configured. Please set the Gemini API key in System Setup."

        from apps.ai import tools as tool_module
        from apps.ai.knowledge import SYSTEM_PROMPT

        tool_module.set_context(user, phone)

        context = caption or "User sent a property-related image."
        prompt = (
            f"The user sent an image via WhatsApp with caption: '{context}'.\n"
            "Analyze this image in the context of Pakistani real estate. "
            "If it's a property document, extract key fields (owner, CNIC, property address, "
            "area, registration number). "
            "If it's a property photo, give a brief assessment. "
            "Flag anything suspicious or tampered."
        )

        try:
            reply = backend.analyze_image(image_bytes, mime_type, prompt)
        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            reply = "Unable to analyze the image right now. Please describe what you need help with."

        history = self._load_history(phone)
        self._save_history(phone, history, f"[image: {caption or 'no caption'}]", reply)
        return reply

    def verify_document_image(self, phone: str, image_bytes: bytes, mime_type: str,
                               caption: str = '', user=None) -> str:
        """
        Specialized document OCR flow.
        Detects document type, extracts fields, flags issues, saves to DocumentScan.
        """
        backend = self._get_backend()
        doc_type = self._detect_doc_type(caption)
        prompt   = self._ocr_prompt(doc_type, caption)

        try:
            raw_ocr = backend.analyze_image(image_bytes, mime_type, prompt)
        except Exception as exc:
            logger.error(f"Document OCR failed: {exc}")
            return (
                "I couldn't read this document right now.\n"
                "Please ensure the image is clear and well-lit, then try again."
            )

        if not raw_ocr or len(raw_ocr.strip()) < 20:
            return (
                "The document image is too blurry or unclear to read.\n"
                "Please send a clearer photo with good lighting."
            )

        result   = self._parse_ocr_response(raw_ocr, doc_type)
        summary  = self._format_doc_summary(result, doc_type)

        # Save to DB
        try:
            from apps.verification.models import DocumentScan
            DocumentScan.objects.create(
                user=user,
                phone=phone,
                document_type=doc_type,
                owner_name=result.get('owner_name', ''),
                cnic_number=result.get('cnic', ''),
                property_address=result.get('address', ''),
                area=result.get('area', ''),
                registration_number=result.get('registration_number', ''),
                issue_date=result.get('date', ''),
                authority=result.get('authority', ''),
                extracted_fields=result,
                red_flags=result.get('flags', []),
                confidence=result.get('confidence', 'LOW'),
                raw_ocr=raw_ocr,
                whatsapp_summary=summary,
                status='suspicious' if result.get('flags') else 'clean',
            )
        except Exception as exc:
            logger.error(f"DocumentScan save failed: {exc}")

        history = self._load_history(phone)
        self._save_history(phone, history, f"[document: {caption or doc_type}]", summary)
        return summary

    # ─── Document OCR helpers ─────────────────────────────────────────────────

    _DOC_KEYWORDS = {
        'fard':             'fard',
        'allotment':        'allotment',
        'registry':         'sale_deed',
        'sale deed':        'sale_deed',
        'deed':             'sale_deed',
        'noc':              'noc',
        'no objection':     'noc',
        'tax certificate':  'tax_cert',
        'tax cert':         'tax_cert',
        'cvt':              'tax_cert',
        'cnic':             'cnic',
        'identity':         'cnic',
        'poa':              'poa',
        'power of attorney':'poa',
    }

    @classmethod
    def _detect_doc_type(cls, caption: str) -> str:
        cap = caption.lower()
        for keyword, doc_type in cls._DOC_KEYWORDS.items():
            if keyword in cap:
                return doc_type
        return 'other'

    @staticmethod
    def _ocr_prompt(doc_type: str, caption: str) -> str:
        type_guidance = {
            'fard': (
                "This is a Fard (ownership record) from PLRA or revenue department. "
                "Extract: owner name, CNIC number, Khasra/Khatuni number, property address, "
                "area (ruqba in marla/kanal), registration date, issuing authority."
            ),
            'allotment': (
                "This is an Allotment Letter from a housing authority. "
                "Extract: allottee name, CNIC, plot/house number, scheme/society name, "
                "area, allotment date, authority name, ballot number if visible."
            ),
            'sale_deed': (
                "This is a Sale Deed or Registry document. "
                "Extract: buyer name, seller name, buyer CNIC, seller CNIC, "
                "property address, area, sale price (PKR), registration date, "
                "Sub-Registrar office, stamp duty paid."
            ),
            'noc': (
                "This is a No Objection Certificate (NOC). "
                "Extract: applicant name, property address, issuing authority (LDA/DHA/CDA), "
                "NOC number, issue date, expiry date if any, purpose of NOC."
            ),
            'tax_cert': (
                "This is a property tax certificate. "
                "Extract: property owner name, property address, tax amount, "
                "tax year, payment date, FBR or local body reference number."
            ),
            'cnic': (
                "This is a Pakistani CNIC card. "
                "Extract: full name, CNIC number (format: XXXXX-XXXXXXX-X), "
                "date of birth, issue date, expiry date, address."
            ),
            'poa': (
                "This is a Power of Attorney document. "
                "Extract: principal name, principal CNIC, attorney name, attorney CNIC, "
                "scope of authority, property details if mentioned, "
                "notary registration number, date, expiry if any."
            ),
        }.get(doc_type, "This is a property-related document.")

        return (
            f"{type_guidance}\n\n"
            "Also check for these red flags:\n"
            "- Overwriting, cutting, or corrections on important fields\n"
            "- Blurred or missing official stamps/signatures\n"
            "- Mismatch between names and CNIC numbers\n"
            "- Photocopied or digitally altered stamps\n"
            "- Missing registration numbers\n\n"
            "Return your response in this exact format:\n"
            "OWNER: [name or N/A]\n"
            "CNIC: [number or N/A]\n"
            "ADDRESS: [property address or N/A]\n"
            "AREA: [area in marla/kanal or N/A]\n"
            "REG_NUMBER: [registration/reference number or N/A]\n"
            "DATE: [issue/registration date or N/A]\n"
            "AUTHORITY: [issuing body or N/A]\n"
            "FLAGS: [comma-separated red flags, or NONE]\n"
            "CONFIDENCE: [HIGH / MEDIUM / LOW — based on image clarity]\n"
            "NOTES: [any additional important observations]\n\n"
            f"User caption: '{caption}'"
        )

    @staticmethod
    def _parse_ocr_response(raw: str, doc_type: str) -> dict:
        result = {
            'owner_name': '', 'cnic': '', 'address': '', 'area': '',
            'registration_number': '', 'date': '', 'authority': '',
            'flags': [], 'confidence': 'LOW', 'notes': '',
        }
        field_map = {
            'OWNER':      'owner_name',
            'CNIC':       'cnic',
            'ADDRESS':    'address',
            'AREA':       'area',
            'REG_NUMBER': 'registration_number',
            'DATE':       'date',
            'AUTHORITY':  'authority',
            'CONFIDENCE': 'confidence',
            'NOTES':      'notes',
        }
        for line in raw.splitlines():
            if ':' not in line:
                continue
            key, _, val = line.partition(':')
            key = key.strip().upper()
            val = val.strip()
            if not val or val == 'N/A':
                continue
            if key == 'FLAGS':
                result['flags'] = [f.strip() for f in val.split(',') if f.strip().lower() != 'none']
            elif key in field_map:
                result[field_map[key]] = val
        return result

    @staticmethod
    def _format_doc_summary(result: dict, doc_type: str) -> str:
        doc_labels = {
            'fard': 'Fard (Ownership Record)',
            'allotment': 'Allotment Letter',
            'sale_deed': 'Sale Deed / Registry',
            'noc': 'NOC',
            'tax_cert': 'Tax Certificate',
            'cnic': 'CNIC',
            'poa': 'Power of Attorney',
            'other': 'Property Document',
        }
        label     = doc_labels.get(doc_type, 'Document')
        flags     = result.get('flags', [])
        confidence = result.get('confidence', 'LOW')
        status_icon = '✅' if not flags else '⚠️'

        lines = [
            f"📄 *DOCUMENT SCAN — {label}*",
            f"Confidence: {confidence} | Status: {'CLEAN' if not flags else 'SUSPICIOUS'} {status_icon}",
            "",
        ]

        field_labels = {
            'owner_name':          '👤 Owner',
            'cnic':                '🪪 CNIC',
            'address':             '📍 Address',
            'area':                '📐 Area',
            'registration_number': '🔢 Ref/Reg No',
            'date':                '📅 Date',
            'authority':           '🏛 Authority',
        }
        for field, lbl in field_labels.items():
            val = result.get(field, '').strip()
            if val:
                lines.append(f"{lbl}: {val}")

        if result.get('notes'):
            lines += ['', f"📝 Notes: {result['notes']}"]

        if flags:
            lines += ['', '⚠️ *RED FLAGS DETECTED:*']
            for f in flags:
                lines.append(f"• {f}")
            lines += [
                '',
                '*Recommendation:* Do NOT proceed with this document until all flags are resolved.',
                'Consult a property lawyer or visit the issuing authority to verify.'
            ]
        else:
            lines += [
                '',
                '✅ No obvious red flags detected in this document.',
                '_Always verify originals at the issuing authority before any transaction._',
            ]

        return '\n'.join(lines)

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        """
        Transcribe a voice message.
        Primary: uses the configured backend (Gemini supports audio; Ollama does not).
        Fallback: if the primary backend can't transcribe, tries Gemini directly
        (works even when AI_BACKEND=local, as long as GEMINI_API_KEY is set).
        """
        try:
            result = self._get_backend().transcribe_audio(audio_bytes, mime_type)
            if result:
                return result
        except Exception as exc:
            logger.error(f"Primary backend audio transcription failed: {exc}")

        # Primary backend returned '' (Ollama) or failed — try Gemini as fallback
        try:
            from apps.config.services import SystemConfigService
            from django.conf import settings as _s
            gemini_key = SystemConfigService.get('gemini_api_key') or _s.GEMINI_API_KEY
            if gemini_key:
                from apps.ai.backends.gemini import GeminiBackend
                fallback = GeminiBackend()
                result = fallback.transcribe_audio(audio_bytes, mime_type)
                if result:
                    logger.info("Audio transcribed via Gemini fallback")
                    return result
        except Exception as exc:
            logger.error(f"Gemini fallback transcription failed: {exc}")

        return ''

    def clear_history(self, phone: str):
        cache.delete(HISTORY_KEY.format(phone=phone))

    # ─── History management ───────────────────────────────────────────────────

    def _load_history(self, phone: str) -> list:
        data = cache.get(HISTORY_KEY.format(phone=phone))
        return data if isinstance(data, list) else []

    def _save_history(self, phone: str, old_history: list, user_msg: str, model_reply: str):
        updated = old_history + [
            {'role': 'user',  'text': user_msg[:2000]},
            {'role': 'model', 'text': model_reply[:2000]},
        ]
        if len(updated) > MAX_TURNS * 2:
            updated = updated[-(MAX_TURNS * 2):]
        cache.set(HISTORY_KEY.format(phone=phone), updated, HISTORY_TTL)

    # ─── Greeting detection ───────────────────────────────────────────────────

    _GREETINGS = {
        'hi', 'hello', 'hey', 'helo', 'hii', 'hiii',
        'aoa', 'aoa!', 'salam', 'salaam', 'slam',
        'assalam o alaikum', 'assalamualaikum', 'assalam', 'as salam',
        'walaikum assalam', 'wa alaikum assalam',
        'good morning', 'good afternoon', 'good evening', 'good night',
        'start', 'help',
    }

    @classmethod
    def _is_greeting(cls, message: str) -> bool:
        return message.strip().lower().rstrip('!?.') in cls._GREETINGS

    @staticmethod
    def _greeting_reply(message: str) -> str:
        from apps.config.services import SystemConfigService
        features = SystemConfigService.get_features()

        text = message.strip().lower().rstrip('!?.')
        urdu_greetings = {'aoa', 'salam', 'salaam', 'slam', 'assalam o alaikum',
                          'assalamualaikum', 'assalam', 'as salam'}

        EN_LINES = {
            'feature_property_search':       "🔍 *Property Search* — Live listings from Zameen, Graana & local DB",
            'feature_property_listing':      "📋 *List Your Property* — Sell or rent via WhatsApp",
            'feature_tax_advice':            "💰 *Tax Advice* — Section 7E, CGT, rental & withholding tax",
            'feature_loan_eligibility':      "🏦 *Loan Eligibility* — Apna Ghar scheme & bank financing",
            'feature_scam_check':            "🛡️ *Scam/Fraud Check* — Verify any deal or agent",
            'feature_document_verification': "📄 *Document Verification* — Send a photo of any property paper",
            'feature_talk_to_agent':         "🤝 *Talk to an Agent* — Connect with a verified local agent",
            'feature_deal_lock':             "🔒 *Deal Lock* — Reserve a property with a token payment",
            'feature_property_audit':        "📊 *Property Audit* — Full risk & investment report (PDF)",
        }
        UR_LINES = {
            'feature_property_search':       "🔍 *Property Search* — Zameen, Graana aur local listings se",
            'feature_property_listing':      "📋 *Property Listing* — Apni property list karein buyers ke liye",
            'feature_tax_advice':            "💰 *Tax Advice* — Section 7E, CGT, rental tax",
            'feature_loan_eligibility':      "🏦 *Loan Eligibility* — Apna Ghar scheme aur bank financing",
            'feature_scam_check':            "🛡️ *Scam/Fraud Check* — Kisi bhi deal ka risk check karein",
            'feature_document_verification': "📄 *Document Check* — Property papers ki photo bhejein",
            'feature_talk_to_agent':         "🤝 *Agent se Baat* — Verified agent se connect karein",
            'feature_deal_lock':             "🔒 *Deal Lock* — Token de kar property reserve karein",
            'feature_property_audit':        "📊 *Property Audit* — Detailed risk aur investment report",
        }

        if text in urdu_greetings:
            lines = '\n'.join(
                f"{i+1}. {line}"
                for i, (k, line) in enumerate(UR_LINES.items())
                if features.get(k, True)
            )
            return (
                "Wa Alaikum Assalam! 🙏\n\n"
                "Main *RealTron AI* hoon — aapka AI-powered real estate assistant.\n\n"
                "Main aapki in chezon mein madad kar sakta hoon:\n\n"
                f"{lines}\n\n"
                "Aap kya dhundh rahe hain? 🏠"
            )

        lines = '\n'.join(
            f"{i+1}. {line}"
            for i, (k, line) in enumerate(EN_LINES.items())
            if features.get(k, True)
        )
        return (
            "Hello! 👋 Welcome to *RealTron AI* — your AI-powered real estate assistant.\n\n"
            "Here's what I can help you with:\n\n"
            f"{lines}\n\n"
            "What are you looking for today? 🏠"
        )

    # ─── Agent request detection (bypass model to prevent hallucination) ────────

    # Words that express intent to connect/find someone
    _AGENT_INTENT_WORDS = {
        'connect', 'find', 'refer', 'get', 'need', 'want',
        'talk', 'speak', 'contact', 'assign', 'help', 'send',
        'chahiye', 'milao', 'dhundo', 'batao',
    }

    # Words that identify the target as a person/agent role
    _AGENT_ROLE_WORDS = {
        'agent', 'someone', 'person', 'anybody', 'anyone', 'somebody',
        'dealer', 'broker', 'consultant', 'representative', 'rep',
        'salesperson', 'sales person', 'sales agent',
        'property agent', 'estate agent', 'property dealer',
        'banda', 'koi', 'kisi',
    }

    # High-confidence standalone phrases — match regardless of other words
    _AGENT_STANDALONE_PHRASES = {
        'talk to agent', 'talk to an agent',
        'agent chahiye', 'agent se baat', 'agent se milao',
        'mujhe agent chahiye', 'dealer chahiye', 'broker chahiye',
        'property wala', 'koi agent', 'koi banda',
        'kisi se baat', 'kisi se milao',
    }

    @classmethod
    def _is_agent_request(cls, message: str) -> bool:
        msg = message.strip().lower()
        # High-confidence exact phrases
        if any(phrase in msg for phrase in cls._AGENT_STANDALONE_PHRASES):
            return True
        # Combination: any intent word + any role word anywhere in the message
        has_intent = any(w in msg for w in cls._AGENT_INTENT_WORDS)
        has_role   = any(w in msg for w in cls._AGENT_ROLE_WORDS)
        return has_intent and has_role

    @staticmethod
    def _handle_agent_request(message: str, history: list, tool_module) -> str:
        """
        Call connect_to_agent directly from Python — no model involved.
        Extracts city/intent/budget from the message using simple keyword rules.
        This is the only reliable way to prevent local models from hallucinating
        agent names and phone numbers.
        """
        import re
        msg = message.lower()

        # ── City extraction ────────────────────────────────────────────────────
        city = ''
        city_map = {
            'lahore': 'Lahore', 'karachi': 'Karachi',
            'islamabad': 'Islamabad', 'rawalpindi': 'Rawalpindi',
            'peshawar': 'Peshawar', 'quetta': 'Quetta',
            'multan': 'Multan', 'faisalabad': 'Faisalabad',
            'sialkot': 'Sialkot', 'gujranwala': 'Gujranwala',
            'hyderabad': 'Hyderabad', 'bahawalpur': 'Bahawalpur',
        }
        for key, name in city_map.items():
            if key in msg:
                city = name
                break

        # If not in message, try last few history turns
        # History format: [{'role': 'user'|'model', 'text': '...'}, ...]
        if not city and history:
            recent = ' '.join(m.get('text', '') for m in history[-6:]).lower()
            for key, name in city_map.items():
                if key in recent:
                    city = name
                    break

        # ── Intent extraction ──────────────────────────────────────────────────
        intent = 'buy'
        if any(w in msg for w in ('sell', 'bechna', 'bech', 'selling')):
            intent = 'sell'
        elif any(w in msg for w in ('rent', 'kiraya', 'lease', 'renting')):
            intent = 'rent'
        elif any(w in msg for w in ('invest', 'investment')):
            intent = 'invest'

        # ── Budget extraction ──────────────────────────────────────────────────
        budget_pkr = 0
        crore_match = re.search(r'(\d+(?:\.\d+)?)\s*crore', msg)
        lakh_match  = re.search(r'(\d+(?:\.\d+)?)\s*lakh', msg)
        if crore_match:
            budget_pkr = int(float(crore_match.group(1)) * 10_000_000)
        elif lakh_match:
            budget_pkr = int(float(lakh_match.group(1)) * 100_000)

        # ── Area extraction ────────────────────────────────────────────────────
        area_keywords = [
            'dha', 'bahria', 'gulberg', 'johar', 'model town', 'defence',
            'clifton', 'f-7', 'f-6', 'f-10', 'g-11', 'i-8', 'blue area',
            'cantt', 'cantonment', 'saddar', 'liberty', 'mall road',
        ]
        specific_area = ''
        for kw in area_keywords:
            if kw in msg:
                specific_area = kw.title()
                break

        result = tool_module.connect_to_agent(
            city=city,
            intent=intent,
            budget_pkr=budget_pkr,
            specific_area=specific_area,
        )
        return result.get('whatsapp_summary') or result.get('message', 'Sorry, could not find an agent right now.')

    # ─── Error handling ───────────────────────────────────────────────────────

    @staticmethod
    def _error_reply() -> str:
        return (
            "AI service is temporarily unavailable. Please try again in a few minutes.\n\n"
            "For urgent help: type *menu* to see what I can do."
        )

    # ─── Logging ──────────────────────────────────────────────────────────────

    def _log(self, user, message: str, reply: str, response_ms: int):
        try:
            from apps.ai.models import AIInteraction
            AIInteraction.objects.create(
                user=user,
                interaction_type='intent_classify',
                model_used=self._backend.label if self._backend else 'unknown',
                response_ms=response_ms,
                input_data={'message': message[:300]},
                output_data={'reply': reply[:300]},
            )
        except Exception:
            pass


# Module-level singleton
_agent_instance = None


def get_agent() -> RealTronAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = RealTronAgent()
    return _agent_instance
