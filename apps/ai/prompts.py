"""
Prompt registry (T-1120, ADR-028).

Every prompt the product sends to a model is a named, versioned
:class:`Prompt` registered here (or by a market pack). Calls go through
``apps.ai.llm``, which renders the prompt and records ``name``, ``version``
and ``digest`` (sha256 of the template) on the ``AIInteraction`` row, so any
AI output can be traced to the exact template that produced it.

Rules:
* Change a template → bump its ``version`` (``tests/test_llm.py`` pins the
  digests, so an edit without a bump fails CI).
* Templates are market-neutral; market facts come from variables filled by
  :func:`market_vars` (name, currency, area unit, price format, tax system)
  and from the market pack fragment ``market.<CC>.knowledge``.
* ``{name}`` placeholders; literal braces are doubled (``{{``). A missing
  variable raises :class:`PromptError` instead of sending a broken prompt.
"""
from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass


class PromptError(KeyError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str
    description: str = ''

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()[:12]

    @property
    def variables(self) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(self.template) if f}

    def render(self, **values) -> 'Rendered':
        missing = self.variables - values.keys()
        if missing:
            raise PromptError(f'prompt {self.name} v{self.version}: missing {sorted(missing)}')
        return Rendered(self.template.format(**values), self)


@dataclass(frozen=True)
class Rendered:
    text: str
    prompt: Prompt

    @property
    def ref(self) -> dict:
        return {'prompt_name': self.prompt.name, 'prompt_version': self.prompt.version,
                'prompt_digest': self.prompt.digest}


_REGISTRY: dict[str, Prompt] = {}


def register(prompt: Prompt) -> Prompt:
    old = _REGISTRY.get(prompt.name)
    if old and old.version == prompt.version and old.template != prompt.template:
        raise PromptError(f'prompt {prompt.name} v{prompt.version} registered twice with different text')
    _REGISTRY[prompt.name] = prompt
    return prompt


def get(name: str) -> Prompt:
    _load_market_fragments()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise PromptError(f'unknown prompt {name}') from None


def all_prompts() -> list[Prompt]:
    _load_market_fragments()
    return sorted(_REGISTRY.values(), key=lambda p: p.name)


def render(name: str, **values) -> Rendered:
    return get(name).render(**values)


# ── Market variables and fragments ────────────────────────────────────────────

def market_vars(country: str | None = None) -> dict:
    """Neutral facts about a market for prompt variables (never raises)."""
    from apps.markets.services import country_or_default, get_market
    code = country_or_default(country)
    info = get_market(code)
    if info is None:
        return {'market_name': 'the local market', 'currency': '', 'area_unit': 'sqm',
                'price_format': '', 'tax_system': ''}
    return {'market_name': info.name or code, 'currency': info.default_currency,
            'area_unit': info.default_area_unit, 'price_format': info.price_format or '',
            'tax_system': info.tax_system or ''}


_MARKET_PACKS = ('PK', 'AE', 'GB', 'US')
_fragments_loaded = False


def _load_market_fragments():
    """Register ``market.<CC>.knowledge`` from each pack (KNOWLEDGE_VERSION, default 1)."""
    global _fragments_loaded
    if _fragments_loaded:
        return
    import importlib
    for cc in _MARKET_PACKS:
        try:
            mod = importlib.import_module(f'apps.markets.{cc.lower()}.knowledge')
        except ImportError:
            continue
        register(Prompt(f'market.{cc}.knowledge', getattr(mod, 'KNOWLEDGE_VERSION', 1),
                        mod.MARKET_KNOWLEDGE.replace('{', '{{').replace('}', '}}'),
                        f'{cc} market knowledge fragment (market pack)'))
    _fragments_loaded = True


def market_fragment(country: str | None) -> Rendered | None:
    from apps.markets.services import country_or_default
    code = country_or_default(country)
    code = 'GB' if code == 'UK' else code
    try:
        return get(f'market.{code}.knowledge').render()
    except PromptError:
        return None


# ── Library ───────────────────────────────────────────────────────────────────
# (agent.system is registered in apps/ai/knowledge.py next to its text.)

register(Prompt('property.score', 2, """You are a {market_name} real estate investment analyst scoring a property.

Property details:
- Title: {title}
- City: {city}
- Location: {location}
- Type: {property_type}
- Area: {area}
- Price: {currency} {price}
- Legal status: {legal_status}
- Construction status: {construction_status}
- Furnished: {furnished_status}

Pre-computed market signals (use these as anchors):
- Location tier: {location_tier} (tier_1=premium, tier_2=mid, tier_3=developing)
- Price vs area benchmark: {price_signal} (fair/underpriced/overpriced)
- Listing completeness: {completeness_pct}%
- Passed verifications: {passed_verifications}
- Supporting documents submitted: {document_count}
- Deterministic baseline score: {baseline_score}/100

Adjust the baseline score up or down based on your {market_name} real estate expertise.
Consider: location growth potential, price trend, infrastructure, demand, and any red flags.
Keep your final score within 15 points of the baseline unless there is a strong reason.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "score": <0-100 integer>,
  "risk": "low" | "medium" | "high",
  "factors": ["specific reason 1", "specific reason 2", "specific reason 3"],
  "suggestion": "one-line actionable investor recommendation"
}}
""", 'AI listing score (properties.tasks); v1 was the Pakistan-only text'))

register(Prompt('fraud.check', 2, """You are a real estate fraud detection assistant for {market_name}.

Input: "{query}"

Check for red flags such as:
- Unregistered or blacklisted agents
- Disputed or duplicate title records
- Common scam patterns in {market_name} (fake allotment files, double sale, advance-fee requests, etc.)

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "risk": "low" | "medium" | "high",
  "flags": ["flag 1", "flag 2"],
  "recommendation": "<one paragraph max>",
  "verify_steps": ["actionable step 1", "actionable step 2"]
}}
""", 'Free-text scam check (verification.services)'))

register(Prompt('search.verdicts', 2, """You are a real estate advisor in {market_name}. Give a one-line investment verdict for each listing.

Listings:
{listings}

Be concise, practical, and specific to the {market_name} market. Prices are in {currency}.

Output ONLY a JSON object. No prose, no markdown fences.
Schema: {{"verdicts": [{{"id": "<source_id>", "verdict": "<one sentence max>"}}]}}
""", 'One-line verdicts on search results (properties.search)'))

register(Prompt('document.ocr', 2, """You are extracting structured data from a property document image from {market_name}.

Extract the following fields if present:
- registry_number
- owner_name
- national_id
- property_address
- area (number) and area_unit (as written, e.g. {area_unit})
- property_type
- mutation_date (or transfer / registration date)
- registrar_office

If a field isn't visible, use null. Also flag anything that looks tampered or inconsistent.

Output ONLY a JSON object. No prose, no markdown fences.
Schema:
{{
  "fields": {{}},
  "tamper_flags": [],
  "overall_confidence": 0.0
}}
""", 'Document OCR (verification.tasks)'))

register(Prompt('lead.summary', 1, """You are a CRM assistant for a real estate platform in {market_name}.
Lead info:
Status: {status}
Budget: {budget_min} – {budget_max} {currency}
City interest: {city}

Conversation (last {count} messages):
{transcript}

Write a concise 3–5 sentence summary covering:
1. What the client is looking for
2. Key discussion points and preferences expressed
3. Current status and suggested next step for the agent
Reply in English only. Be direct and factual.""", 'Lead conversation summary (leads.views)'))

register(Prompt('lead.suggest_replies', 1, """You are a real estate agent assistant for a platform in {market_name}.
Lead status: {status} | City: {city} | Budget: {budget_max} {currency}

Recent conversation:
{recent}

Last client message: {last_inbound}

Generate exactly 3 short, professional WhatsApp reply suggestions for the agent.
Each should be 1–2 sentences, in the language and style the client uses.
Format as JSON array of strings: ["reply1", "reply2", "reply3"].
No explanation, just the JSON array.""", 'Agent reply suggestions (leads.views)'))

register(Prompt('campaign.reengage.system', 1, """You are a real estate sales assistant for {org_name}. Generate a single WhatsApp re-engagement message (max {max_chars} characters) for a lead who went quiet 72+ hours ago. Tone: warm, helpful, not pushy. Language: {language}. Do NOT use more than one emoji. Never mention internal CRM terms.""",
    'Re-engagement message: system part (campaigns.re_engagement)'))

register(Prompt('campaign.reengage.user', 1, """Lead profile:
- Intent: {intent}
- City: {city}
- {budget}

Recent conversation:
{transcript}

CRM activity:
{crm_timeline}

Write one short WhatsApp message (max {max_chars} chars) that picks up naturally from this conversation.""",
    'Re-engagement message: user part (campaigns.re_engagement)'))
