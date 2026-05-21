"""
Dynamic context builder for the RealTron AI system prompt.

Called once per request by AIServiceManager. Builds a context block that is
appended to the base system prompt in knowledge.py with three sections:
  1. Active organisation rules & live inventory sample
  2. Active lead state (intent, score, budget, language preference)
  3. Market configuration (currency, size unit, tax system, price convention)

All DB queries are wrapped in broad try/except so a DB failure never kills
the AI response. Slow queries are guarded by a single DB hit per section.
"""
from __future__ import annotations

import logging

from apps.markets.registry import MarketConfig, MARKET_REGISTRY, get_market_config  # noqa: F401
from apps.markets.phone import PhoneCountryResolver

logger = logging.getLogger(__name__)


# ── Dynamic context builder ────────────────────────────────────────────────────

class DynamicContextBuilder:
    """
    Assembles the per-request context block injected into the system prompt.
    """

    @classmethod
    def build(cls, user=None, organization=None, phone: str = '') -> str:
        """
        Returns a multi-section context string to append to the base system prompt.
        Never raises — returns '' on total failure.
        """
        try:
            blocks = []

            org_block = cls._org_block(organization)
            if org_block:
                blocks.append(org_block)

            lead_block = cls._lead_block(user)
            if lead_block:
                blocks.append(lead_block)

            country = cls._resolve_country(user, organization, phone)
            market_block = cls._market_block(country)
            blocks.append(market_block)

            return '\n\n'.join(blocks)
        except Exception as exc:
            logger.error("DynamicContextBuilder.build failed: %s", exc)
            return ''

    # ── Organisation section ───────────────────────────────────────────────────

    @staticmethod
    def _org_block(org) -> str:
        if org is None:
            return ''
        try:
            lines = [f'═══ ACTIVE ORGANISATION: {org.name} ({org.get_org_type_display()}) ═══']

            if org.city:
                lines.append(f'HQ City: {org.city}')
            if org.country and org.country != 'PK':
                lines.append(f'Country: {org.country}')
            if org.plan:
                lines.append(f'Plan: {org.plan}')

            # Enabled feature flags for this org (skip defaults — only show overrides)
            try:
                from apps.organizations.models import OrganizationConfig
                overrides = OrganizationConfig.objects.filter(
                    organization=org
                ).values_list('key', 'value')
                disabled = [k for k, v in overrides if v == 'false']
                if disabled:
                    lines.append(f'Disabled features: {", ".join(disabled)}')
            except Exception:
                pass

            # Language instruction — overrides default English when org has a non-English locale
            try:
                _LANG_NAMES = {
                    'ar': 'Arabic', 'ur': 'Urdu', 'fr': 'French',
                    'zh': 'Chinese (Simplified)', 'es': 'Spanish',
                }
                org_lang = getattr(org, 'language', '') or ''
                if not org_lang or org_lang == 'en':
                    # Derive from market config when org hasn't set an explicit preference
                    from apps.markets.registry import get_market_config
                    mk_lang = get_market_config(org.country or 'PK').language
                    org_lang = mk_lang if mk_lang != 'en' else ''
                if org_lang and org_lang != 'en':
                    lang_name = _LANG_NAMES.get(org_lang, org_lang.upper())
                    lines.append(
                        f'LANGUAGE DIRECTIVE: Respond in {lang_name} by default. '
                        f'Switch to English only if the user explicitly writes in English.'
                    )
            except Exception:
                pass

            # Live inventory sample — top 5 active properties
            try:
                from apps.properties.models import Property
                sample = list(
                    Property.objects
                    .filter(organization=org, is_active=True)
                    .order_by('-created_at')
                    .values('title', 'city', 'location', 'price', 'property_type')[:5]
                )
                if sample:
                    lines.append('Inventory sample (live):')
                    for p in sample:
                        price = f"PKR {p['price']:,}" if p['price'] else 'POA'
                        lines.append(
                            f"  • {p['title']} — {p['location']}, {p['city']} · {price}"
                        )
            except Exception:
                pass

            return '\n'.join(lines)
        except Exception as exc:
            logger.debug("_org_block failed: %s", exc)
            return ''

    # ── Lead section ───────────────────────────────────────────────────────────

    @staticmethod
    def _lead_block(user) -> str:
        if user is None:
            return ''
        try:
            from apps.leads.models import Lead

            lead = (
                Lead.objects
                .filter(user=user)
                .order_by('-created_at')
                .only('intent', 'score', 'intent_signals',
                      'city_interest', 'budget_max', 'budget_currency',
                      'status', 'routing_state')
                .first()
            )
            if not lead:
                return ''

            lines = ['═══ ACTIVE LEAD CONTEXT ═══']

            _intent_labels = {
                'buy': 'looking to BUY', 'sell': 'looking to SELL',
                'rent': 'looking to RENT', 'invest': 'looking to INVEST',
                'loan': 'enquiring about LOAN', 'tax': 'seeking TAX ADVICE',
            }
            if lead.intent:
                lines.append(f'Intent: {_intent_labels.get(lead.intent, lead.intent)}')

            if lead.city_interest:
                lines.append(f'City interest: {lead.city_interest}')

            if lead.budget_max:
                currency = getattr(lead, 'budget_currency', 'PKR') or 'PKR'
                lines.append(f'Budget: up to {currency} {lead.budget_max:,}')

            if lead.score:
                tier = (
                    'HOT LEAD — prioritise closing and offer agent connection'
                    if lead.score >= 75
                    else 'WARM LEAD' if lead.score >= 45
                    else 'COLD LEAD — nurture gently'
                )
                lines.append(f'Lead score: {lead.score}/100 ({tier})')

            # Language from intent_signals (set by WhatsApp router on first interaction)
            signals = lead.intent_signals or {}
            if signals.get('language'):
                lines.append(f'Preferred language: {signals["language"]}')

            if lead.status:
                lines.append(f'CRM status: {lead.status}')

            return '\n'.join(lines)
        except Exception as exc:
            logger.debug("_lead_block failed: %s", exc)
            return ''

    # ── Market / country section ───────────────────────────────────────────────

    @staticmethod
    def _resolve_country(user, org, phone: str = '') -> str:
        """Priority: org.country (explicit) → phone dial code → user.country → 'PK'.

        Orgs configure their country intentionally, so it always wins.
        Phone dial code provides automatic localisation for org-less interactions.
        """
        try:
            if org and getattr(org, 'country', ''):
                return org.country.upper()
        except Exception:
            pass
        if phone:
            resolved = PhoneCountryResolver.resolve(phone)
            if resolved:
                return resolved
        try:
            if user and getattr(user, 'country', ''):
                return user.country.upper()
        except Exception:
            pass
        return 'PK'

    @staticmethod
    def _market_block(country: str) -> str:
        cfg = get_market_config(country)
        return (
            '═══ MARKET CONFIGURATION ═══\n'
            f'Country : {cfg.country}  |  Currency : {cfg.currency} ({cfg.currency_sym})\n'
            f'Size unit: {cfg.size_unit}  ({cfg.sqft_per_unit} sqft each)\n'
            f'Tax system: {cfg.tax_system}\n'
            f'Price convention: {cfg.price_fmt}\n'
            f'RULE: Always express prices in {cfg.currency} and sizes in {cfg.size_unit}. '
            f'Convert any user input to these units before calling tools.'
        )
