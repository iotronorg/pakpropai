"""
Compliance Filter & Hallucination Detection Test Suite — RealTron AI.

Run:
    python manage.py test tests.test_compliance_filters --settings=config.settings.test
    # or via pytest-django:
    pytest tests/test_compliance_filters.py -v

Coverage:
    - Price extraction from free-form LLM text (crore / lakh / million / raw PKR)
    - Price variance detection: >10% deviation triggers ComplianceResult(safe=False)
    - Price within threshold (≤10%): passes ComplianceResult(safe=True)
    - False installment promise: AI claims installments when DB has installment_available=False
    - City/location mismatch: AI mentions Lahore when property is in Karachi
    - Lead routing_state escalated to AGENT_ASSIGNED on any violation
    - ComplianceViolation DB record created with correct metadata
    - human_reply is non-empty and routes user to agent on violation
    - No-property-context path: returns safe=True (cannot verify without ground truth)
    - Redis cache: property data loaded from cache on second call (DB not re-queried)
    - End-to-end: full mock LLM response intercepted before WhatsApp send
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch, call

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.ai.compliance_filters import (
    ComplianceEngine,
    ComplianceResult,
    PRICE_VARIANCE_THRESHOLD,
    _extract_claims,
    _extract_price_pkr,
    _check_price,
    _block_installment,
    _block_location,
    _reroute_to_agent,
    _persist_violation,
)
from apps.leads.models import Lead
from apps.organizations.models import Organization
from apps.properties.models import Property

User = get_user_model()


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_user(phone='+923001234567'):
    return User.objects.get_or_create(
        phone=phone, defaults={'role': 'client', 'is_active': True}
    )[0]


def _make_org():
    return Organization.objects.get_or_create(
        name='Compliance Test Org',
        defaults={'country': 'PK', 'is_active': True},
    )[0]


def _make_lead(user, org, routing_state=Lead.RoutingState.ORG_QUEUE):
    return Lead.objects.get_or_create(
        user=user,
        defaults={
            'organization': org,
            'routing_state': routing_state,
            'source': Lead.Source.WHATSAPP,
        },
    )[0]


def _make_property(
    org,
    price: int = 25_000_000,
    city: str  = 'Lahore',
    installment_available: bool = False,
):
    return Property.objects.create(
        listing_owner_type='organization',
        organization=org,
        title='Test Villa DHA',
        city=city,
        location='DHA Phase 5',
        property_type='residential',
        price=price,
        currency='PKR',
        area_marla=10,
        installment_available=installment_available,
        is_active=True,
    )


def _prop_dict(prop: Property) -> dict:
    return {
        'id':                   str(prop.id),
        'price':                prop.price,
        'currency':             prop.currency,
        'city':                 prop.city,
        'installment_available': prop.installment_available,
        'organization_id':      str(prop.organization_id),
        'title':                prop.title,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Price Extraction
# ──────────────────────────────────────────────────────────────────────────────

class TestPriceExtraction(TestCase):

    def _assert_price(self, text: str, expected: int, label: str = ''):
        result = _extract_price_pkr(text)
        self.assertEqual(result, expected, f"{label}: expected {expected:,}, got {result}")

    def test_crore_format(self):
        self._assert_price("This property is priced at 2.5 crore.", 25_000_000, "2.5 crore")

    def test_lakh_format(self):
        self._assert_price("The token amount is 50 lakh.", 5_000_000, "50 lakh")

    def test_million_format(self):
        self._assert_price("Market value around 3 million PKR.", 3_000_000, "3 million")

    def test_raw_pkr_format(self):
        self._assert_price("Listed at PKR 2,50,00,000.", 25_000_000, "raw PKR notation")

    def test_rs_format(self):
        self._assert_price("Priced at Rs. 1,50,00,000.", 15_000_000, "Rs. notation")

    def test_picks_largest_when_multiple_mentioned(self):
        text = "Down payment 50 lakh, total price 2.5 crore."
        result = _extract_price_pkr(text)
        self.assertEqual(result, 25_000_000)  # 2.5 crore > 50 lakh

    def test_returns_none_when_no_price(self):
        result = _extract_price_pkr("Great property in DHA with good views.")
        self.assertIsNone(result)

    def test_fractional_crore(self):
        self._assert_price("Available at 1.75 crore.", 17_500_000, "1.75 crore")

    def test_whole_crore(self):
        self._assert_price("Budget: 3 crore.", 30_000_000, "3 crore")

    def test_lakh_and_crore_in_same_sentence(self):
        text = "The developer is offering 5 lakh discount on the 2 crore price."
        result = _extract_price_pkr(text)
        self.assertEqual(result, 20_000_000)  # 2 crore is larger

    def test_installment_detection_true(self):
        claim = _extract_claims("This property has easy installment plan available.")
        self.assertTrue(claim.installments_offered)

    def test_installment_detection_false(self):
        claim = _extract_claims("Full payment only — no installments.")
        # "installments" appears in negation but regex matches the word
        # We only check for the word presence (conservative — humans verify context)
        # This is by design: false positive here → human agent reviews, not harm.
        self.assertTrue(claim.installments_offered)

    def test_no_installment_flag_on_clean_text(self):
        claim = _extract_claims("Beautiful 5 marla house in DHA Lahore at 2.5 crore.")
        self.assertFalse(claim.installments_offered)

    def test_city_extraction(self):
        claim = _extract_claims("This property is located in Lahore DHA Phase 5.")
        self.assertEqual(claim.city, 'lahore')

    def test_city_none_when_not_mentioned(self):
        claim = _extract_claims("Price is 2.5 crore, full payment required.")
        self.assertIsNone(claim.city)


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Price Variance Detection
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestPriceVarianceDetection(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = _make_user()
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)
        # DB truth: property priced at exactly 25,000,000 PKR
        self.prop = _make_property(self.org, price=25_000_000, city='Lahore')

    # ── Violation cases ───────────────────────────────────────────────────────

    def test_price_above_threshold_triggers_violation(self):
        """AI offers 20% discount → should be caught (exceeds 10% threshold)."""
        reply = "Great news! This property is available for just 2 crore (PKR 20,000,000)."
        prop_data = _prop_dict(self.prop)
        with patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = _check_price(
                claimed=20_000_000,
                db_price=25_000_000,
                prop=prop_data,
                phone=self.user.phone,
                org=self.org,
            )
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_price')
        self.assertAlmostEqual(result.variance_pct, 0.20, places=2)
        self.assertEqual(result.db_price, 25_000_000)
        self.assertEqual(result.claimed_price, 20_000_000)

    def test_price_inflated_above_threshold_triggers_violation(self):
        """AI inflates price by 15% (possibly to extract more from buyer)."""
        reply = "The developer has priced this at 2.875 crore."
        prop_data = _prop_dict(self.prop)
        with patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = _check_price(
                claimed=28_750_000,
                db_price=25_000_000,
                prop=prop_data,
                phone=self.user.phone,
                org=self.org,
            )
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_price')

    def test_violation_reroutes_lead_to_agent_assigned(self):
        """Price violation must escalate lead routing state to AGENT_ASSIGNED."""
        prop_data = _prop_dict(self.prop)
        with patch('apps.ai.compliance_filters._persist_violation'):
            _check_price(
                claimed=18_000_000,  # 28% below DB price
                db_price=25_000_000,
                prop=prop_data,
                phone=self.user.phone,
                org=self.org,
            )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.routing_state, Lead.RoutingState.AGENT_ASSIGNED)

    def test_violation_persists_compliance_violation_db_record(self):
        """ComplianceViolation DB record must be created on price violation."""
        from apps.audit.models import ComplianceViolation
        prop_data = _prop_dict(self.prop)
        count_before = ComplianceViolation.objects.count()
        with patch('apps.ai.compliance_filters._reroute_to_agent'):
            _check_price(
                claimed=15_000_000,
                db_price=25_000_000,
                prop=prop_data,
                phone=self.user.phone,
                org=self.org,
            )
        self.assertGreater(ComplianceViolation.objects.count(), count_before)
        record = ComplianceViolation.objects.latest('created_at')
        self.assertEqual(record.violation_type, 'price_variance')
        self.assertEqual(record.phone, self.user.phone)

    def test_violation_returns_human_reply(self):
        """human_reply must be non-empty and guide user to wait for an agent."""
        prop_data = _prop_dict(self.prop)
        with patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = _check_price(
                claimed=10_000_000,
                db_price=25_000_000,
                prop=prop_data,
                phone=self.user.phone,
                org=self.org,
            )
        self.assertTrue(result.human_reply)
        self.assertIn('agent', result.human_reply.lower())

    # ── Safe cases ────────────────────────────────────────────────────────────

    def test_exact_price_match_passes(self):
        prop_data = _prop_dict(self.prop)
        result = _check_price(
            claimed=25_000_000,
            db_price=25_000_000,
            prop=prop_data,
            phone=self.user.phone,
            org=self.org,
        )
        self.assertTrue(result.safe)

    def test_price_within_threshold_passes(self):
        """9% deviation — within the 10% threshold → should pass."""
        prop_data = _prop_dict(self.prop)
        result = _check_price(
            claimed=22_750_000,   # 9% below 25M
            db_price=25_000_000,
            prop=prop_data,
            phone=self.user.phone,
            org=self.org,
        )
        self.assertTrue(result.safe)
        self.assertEqual(result.action_taken, 'none')

    def test_exactly_10_percent_deviation_passes(self):
        """At exactly 10%, the condition is deviation > threshold → passes (≤ is safe)."""
        prop_data = _prop_dict(self.prop)
        result = _check_price(
            claimed=22_500_000,   # exactly 10% below
            db_price=25_000_000,
            prop=prop_data,
            phone=self.user.phone,
            org=self.org,
        )
        self.assertTrue(result.safe)

    def test_zero_db_price_passes_without_error(self):
        """A DB record with price=0 should skip the check to avoid division by zero."""
        prop_data = _prop_dict(self.prop)
        prop_data['price'] = 0
        result = _check_price(
            claimed=5_000_000,
            db_price=0,
            prop=prop_data,
            phone=self.user.phone,
            org=self.org,
        )
        self.assertTrue(result.safe)


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Installment Hallucination
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestInstallmentHallucination(TestCase):

    def setUp(self):
        self.user = _make_user(phone='+923001234568')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)
        self.prop_no_installment = _make_property(
            self.org, price=15_000_000, city='Karachi', installment_available=False
        )
        self.prop_with_installment = _make_property(
            _make_org(),  # different org to avoid collision
            price=20_000_000, city='Islamabad', installment_available=True
        )

    def test_installment_offer_blocked_when_db_says_no(self):
        prop_data = _prop_dict(self.prop_no_installment)
        with patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = _block_installment(prop_data, self.user.phone, self.org)
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_installment')
        self.assertTrue(result.human_reply)

    def test_installment_offer_allowed_when_db_says_yes(self):
        """If installment_available=True in DB, the compliance check should pass."""
        reply = "This property has an easy installment plan — 50% down payment."
        prop_data = _prop_dict(self.prop_with_installment)
        # Simulate check_output using only installment check
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data):
            result = ComplianceEngine.check_output(
                reply,
                org=self.org,
                phone=self.user.phone,
                property_id=str(self.prop_with_installment.id),
            )
        self.assertTrue(result.safe)

    def test_installment_violation_reroutes_lead(self):
        prop_data = _prop_dict(self.prop_no_installment)
        with patch('apps.ai.compliance_filters._persist_violation'):
            _block_installment(prop_data, self.user.phone, self.org)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.routing_state, Lead.RoutingState.AGENT_ASSIGNED)

    def test_installment_violation_persists_db_record(self):
        from apps.audit.models import ComplianceViolation
        prop_data = _prop_dict(self.prop_no_installment)
        count_before = ComplianceViolation.objects.count()
        with patch('apps.ai.compliance_filters._reroute_to_agent'):
            _block_installment(prop_data, self.user.phone, self.org)
        self.assertGreater(ComplianceViolation.objects.count(), count_before)
        record = ComplianceViolation.objects.latest('created_at')
        self.assertEqual(record.violation_type, 'false_installment')


# ──────────────────────────────────────────────────────────────────────────────
# Test class: City / Location Mismatch
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestLocationMismatch(TestCase):

    def setUp(self):
        self.user = _make_user(phone='+923001234569')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)
        self.prop = _make_property(self.org, city='Karachi')

    def test_wrong_city_blocked(self):
        prop_data = _prop_dict(self.prop)
        with patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = _block_location('lahore', 'Karachi', prop_data, self.user.phone, self.org)
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_location')
        self.assertIn('lahore', result.details.lower())
        self.assertIn('karachi', result.details.lower())

    def test_correct_city_passes(self):
        """AI mentions Karachi, DB city is Karachi → should pass."""
        reply = "This beautiful property is located in Karachi DHA at 2 crore."
        prop_data = _prop_dict(self.prop)
        # price 25M vs claimed 20M → borderline; main thing is city passes
        # Use a property with price matching to isolate city check
        prop_data['price'] = 20_000_000  # 20M — within 10% of a 20M claim doesn't matter here
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data):
            result = ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        # City 'karachi' matches DB 'Karachi' — city check passes
        # Price: 2 crore = 20M. DB price patched to 20M → within threshold.
        # So overall: safe
        self.assertTrue(result.safe)

    def test_location_violation_persists_record(self):
        from apps.audit.models import ComplianceViolation
        prop_data = _prop_dict(self.prop)
        count_before = ComplianceViolation.objects.count()
        with patch('apps.ai.compliance_filters._reroute_to_agent'):
            _block_location('lahore', 'Karachi', prop_data, self.user.phone, self.org)
        self.assertGreater(ComplianceViolation.objects.count(), count_before)
        record = ComplianceViolation.objects.latest('created_at')
        self.assertEqual(record.violation_type, 'location_mismatch')


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Full check_output integration
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestCheckOutputIntegration(TestCase):

    def setUp(self):
        self.user = _make_user(phone='+923001234570')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)

    def _run(self, reply: str, prop: Property) -> ComplianceResult:
        prop_data = _prop_dict(prop)
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data):
            return ComplianceEngine.check_output(
                reply,
                org=self.org,
                phone=self.user.phone,
                property_id=str(prop.id),
            )

    def test_safe_reply_with_correct_price_passes(self):
        prop  = _make_property(self.org, price=25_000_000, city='Lahore')
        reply = "This 10 marla villa in Lahore is priced at 2.5 crore."
        result = self._run(reply, prop)
        self.assertTrue(result.safe)
        self.assertEqual(result.action_taken, 'none')

    def test_hallucianted_discount_blocked(self):
        prop  = _make_property(self.org, price=25_000_000, city='Lahore')
        reply = (
            "I have great news! We can offer you this property at a special price "
            "of only 1.5 crore (PKR 15,000,000) — a saving of 40%!"
        )
        result = self._run(reply, prop)
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_price')

    def test_false_installment_in_llm_reply_blocked(self):
        prop  = _make_property(self.org, price=20_000_000, city='Karachi', installment_available=False)
        reply = "You can get this property with an easy installment plan, just 25% down payment."
        result = self._run(reply, prop)
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_installment')

    def test_no_property_context_passes_safely(self):
        """Without a property context, compliance engine cannot verify → pass through."""
        reply = "Great question! Let me help you with that property inquiry."
        result = ComplianceEngine.check_output(reply, org=self.org, phone=self.user.phone)
        self.assertTrue(result.safe)

    def test_reply_with_no_price_or_installment_passes(self):
        prop  = _make_property(self.org, price=30_000_000)
        reply = "The property has 5 bedrooms and a large garden. It is in a great location."
        result = self._run(reply, prop)
        self.assertTrue(result.safe)

    def test_empty_reply_passes(self):
        result = ComplianceEngine.check_output('', org=self.org, phone=self.user.phone)
        self.assertTrue(result.safe)

    def test_whitespace_reply_passes(self):
        result = ComplianceEngine.check_output('   \n  ', org=self.org, phone=self.user.phone)
        self.assertTrue(result.safe)


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Redis Property Cache
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestPropertyCache(TestCase):

    def test_set_active_property_populates_cache(self):
        from django.core.cache import cache
        import json, re
        from apps.ai.compliance_filters import _CACHE_PREFIX, PROPERTY_CACHE_TTL

        phone  = '+923001234571'
        org_id = str(uuid.uuid4())
        data   = {
            'id': str(uuid.uuid4()), 'price': 20_000_000,
            'city': 'Lahore', 'installment_available': False,
        }
        ComplianceEngine.set_active_property(phone, org_id, data)
        key = f"{_CACHE_PREFIX}{org_id}:{phone.lstrip('+')}"
        raw = cache.get(key)
        self.assertIsNotNone(raw)
        self.assertEqual(json.loads(raw)['price'], 20_000_000)

    def test_load_property_by_id_caches_db_result(self):
        """Second call to _load_property_by_id should not hit the DB."""
        from apps.ai.compliance_filters import _load_property_by_id
        org  = _make_org()
        prop = _make_property(org, price=12_000_000)

        # First call — DB hit
        first = _load_property_by_id(str(prop.id))
        self.assertIsNotNone(first)
        self.assertEqual(first['price'], 12_000_000)

        # Second call — cache hit (patch DB to ensure it's not called)
        with patch('apps.properties.models.Property.objects') as mock_qs:
            second = _load_property_by_id(str(prop.id))
        mock_qs.filter.assert_not_called()
        self.assertEqual(second['price'], 12_000_000)


# ──────────────────────────────────────────────────────────────────────────────
# Test class: End-to-end outbound message interception simulation
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestOutboundInterceptionSimulation(TestCase):
    """
    Simulates the full flow:
        LLM generates reply → ComplianceEngine checks → blocks if violated
        → human_reply sent instead of AI reply → WhatsApp API never called with bad data.
    """

    def setUp(self):
        self.user = _make_user(phone='+923001234572')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)

    def _simulate_outbound(
        self,
        ai_reply:   str,
        db_price:   int,
        db_city:    str  = 'Lahore',
        installment: bool = False,
    ) -> tuple[str, bool]:
        """
        Returns (message_sent_to_user, was_wa_api_called_with_ai_reply).
        Simulates the router calling compliance before dispatching to WA.
        """
        prop = _make_property(
            self.org, price=db_price, city=db_city,
            installment_available=installment,
        )
        prop_data = _prop_dict(prop)
        wa_calls: list[str] = []

        def fake_send(phone: str, text: str, **kwargs):
            wa_calls.append(text)
            return {'messages': [{'id': 'fake-wamid'}]}

        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.whatsapp.client.WhatsAppClient.send_text', side_effect=fake_send):

            result = ComplianceEngine.check_output(
                ai_reply,
                org=self.org,
                phone=self.user.phone,
                property_id=str(prop.id),
            )

            if result.safe:
                fake_send(self.user.phone, ai_reply)
                return ai_reply, True
            else:
                # Compliance block: send human handoff, NOT the AI reply
                fake_send(self.user.phone, result.human_reply)
                return result.human_reply, False

    def test_good_reply_reaches_whatsapp(self):
        ai_reply = "This property in Lahore is available at 2.5 crore."
        message_sent, ai_used = self._simulate_outbound(ai_reply, db_price=25_000_000)
        self.assertTrue(ai_used, "Clean reply should be sent via WhatsApp")
        self.assertIn('2.5 crore', message_sent)

    def test_hallucinated_price_never_reaches_whatsapp(self):
        """AI offers 30% discount — should be intercepted before WhatsApp API."""
        ai_reply = "Special offer: only 1.75 crore for this 10 marla property!"
        message_sent, ai_used = self._simulate_outbound(ai_reply, db_price=25_000_000)
        self.assertFalse(ai_used, "Hallucinated price must NOT be sent to user")
        self.assertNotIn('1.75 crore', message_sent)
        self.assertIn('agent', message_sent.lower())

    def test_false_installment_never_reaches_whatsapp(self):
        ai_reply = "You can buy this property with convenient monthly installments — just 30% down!"
        message_sent, ai_used = self._simulate_outbound(
            ai_reply, db_price=20_000_000, installment=False
        )
        self.assertFalse(ai_used)
        self.assertIn('agent', message_sent.lower())

    def test_wrong_city_never_reaches_whatsapp(self):
        ai_reply = "This property is located in Karachi at 1.5 crore."  # DB says Lahore
        message_sent, ai_used = self._simulate_outbound(
            ai_reply, db_price=15_000_000, db_city='Lahore'
        )
        self.assertFalse(ai_used)
        self.assertIn('agent', message_sent.lower())

    def test_lead_escalated_to_agent_on_price_violation(self):
        ai_reply = "This property is available at only 10 lakh — amazing deal!"
        self._simulate_outbound(ai_reply, db_price=25_000_000)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.routing_state, Lead.RoutingState.AGENT_ASSIGNED)


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Multi-currency price extraction
# ──────────────────────────────────────────────────────────────────────────────

class TestMultiCurrencyExtraction(TestCase):
    """_extract_price_with_currency must parse AED, USD, GBP, EUR, SAR, INR notations."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def _assert_extraction(self, text: str, expected_amount: int, expected_currency, label=''):
        from apps.ai.compliance_filters import _extract_price_with_currency
        amount, currency = _extract_price_with_currency(text)
        self.assertEqual(amount, expected_amount,
                         f"{label}: amount — expected {expected_amount:,}, got {amount}")
        self.assertEqual(currency, expected_currency,
                         f"{label}: currency — expected {expected_currency!r}, got {currency!r}")

    def test_aed_prefix(self):
        self._assert_extraction("AED 2,500,000", 2_500_000, 'AED', "AED prefix")

    def test_dollar_sign(self):
        self._assert_extraction("$500,000", 500_000, 'USD', "USD dollar sign")

    def test_gbp_sign(self):
        self._assert_extraction("£750,000", 750_000, 'GBP', "GBP pound sign")

    def test_eur_sign(self):
        self._assert_extraction("€1,200,000", 1_200_000, 'EUR', "EUR euro sign")

    def test_sar_prefix(self):
        self._assert_extraction("SAR 3,000,000", 3_000_000, 'SAR', "SAR prefix")

    def test_inr_sign(self):
        self._assert_extraction("₹2.5 crore", 25_000_000, 'INR', "INR crore")

    def test_suffix_currency_aed(self):
        self._assert_extraction("Available for 5,500,000 AED", 5_500_000, 'AED', "AED suffix")

    def test_suffix_currency_usd(self):
        self._assert_extraction("Listed at 1,200,000 USD", 1_200_000, 'USD', "USD suffix")

    def test_million_usd(self):
        self._assert_extraction("priced at $1.5 million", 1_500_000, 'USD', "USD million")

    def test_million_aed(self):
        self._assert_extraction("AED 3 million", 3_000_000, 'AED', "AED million")

    def test_dirhams_word(self):
        """Currency word 'dirhams' in text should set currency to AED."""
        self._assert_extraction(
            "This apartment costs 1 million dirhams.", 1_000_000, 'AED', "dirham word"
        )

    def test_pounds_word(self):
        self._assert_extraction(
            "The flat is going for 450,000 pounds.", 450_000, 'GBP', "pounds word"
        )

    def test_no_currency_returns_none(self):
        from apps.ai.compliance_filters import _extract_price_with_currency
        amount, currency = _extract_price_with_currency("2.5 crore property in DHA Lahore.")
        self.assertEqual(amount, 25_000_000)
        self.assertIsNone(currency)

    def test_pkr_raw_notation(self):
        self._assert_extraction("PKR 25,000,000", 25_000_000, 'PKR', "PKR prefix")

    def test_backward_compat_extract_price_pkr(self):
        """_extract_price_pkr must still return the integer amount."""
        from apps.ai.compliance_filters import _extract_price_pkr
        self.assertEqual(_extract_price_pkr("AED 5,500,000"), 5_500_000)
        self.assertEqual(_extract_price_pkr("$1.5 million"), 1_500_000)
        self.assertIsNone(_extract_price_pkr("Great property with nice views."))


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Currency mismatch detection
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestCurrencyMismatch(TestCase):
    """AI claiming AED price on a PKR property (or vice versa) must be blocked."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = _make_user(phone='+923001234573')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)
        # PKR property
        self.prop = _make_property(self.org, price=25_000_000, city='Lahore')

    def test_aed_claim_on_pkr_property_blocked(self):
        """AI says AED but DB says PKR → currency mismatch violation."""
        prop_data = _prop_dict(self.prop)  # currency='PKR'
        reply = "This property is available for AED 2,500,000 — a great deal!"
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_currency_mismatch')
        self.assertIn('AED', result.details)
        self.assertIn('PKR', result.details)

    def test_usd_claim_on_pkr_property_blocked(self):
        prop_data = _prop_dict(self.prop)
        reply = "You can get this property for $250,000."
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            result = ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_currency_mismatch')

    def test_currency_mismatch_routes_lead_to_agent(self):
        prop_data = _prop_dict(self.prop)
        reply = "The price is AED 2,500,000."
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.ai.compliance_filters._persist_violation'):
            ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.routing_state, Lead.RoutingState.AGENT_ASSIGNED)

    def test_currency_mismatch_persists_violation_record(self):
        from apps.audit.models import ComplianceViolation
        prop_data = _prop_dict(self.prop)
        reply = "£250,000 is the asking price for this property."
        count_before = ComplianceViolation.objects.count()
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.ai.compliance_filters._reroute_to_agent'):
            ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        self.assertGreater(ComplianceViolation.objects.count(), count_before)
        record = ComplianceViolation.objects.latest('created_at')
        self.assertEqual(record.violation_type, 'currency_mismatch')

    def test_same_currency_aed_passes_when_db_is_aed(self):
        """AED claim on AED property → currency matches → no currency violation."""
        prop_data = _prop_dict(self.prop)
        prop_data['currency'] = 'AED'
        prop_data['price'] = 2_500_000
        reply = "This property is available for AED 2,500,000."
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data):
            result = ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        self.assertTrue(result.safe)

    def test_no_currency_in_reply_skips_currency_check(self):
        """If AI doesn't mention currency (e.g. just 'crore'), skip currency mismatch."""
        prop_data = _prop_dict(self.prop)
        reply = "This 10 marla property is available at 2.5 crore."
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data):
            result = ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(self.prop.id),
            )
        # No currency in reply → skip mismatch check; only price variance applies
        self.assertTrue(result.safe)  # 2.5 crore = 25M = exact DB price → safe


# ──────────────────────────────────────────────────────────────────────────────
# Test class: Global city / location mismatch detection
# ──────────────────────────────────────────────────────────────────────────────

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class TestGlobalCityMismatch(TestCase):
    """Multilingual city names (Arabic, Hindi, Russian, Chinese) trigger location mismatch."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = _make_user(phone='+923001234574')
        self.org  = _make_org()
        self.lead = _make_lead(self.user, self.org)

    def _check(self, reply: str, db_city: str, prop_price: int = 5_000_000) -> ComplianceResult:
        prop = _make_property(self.org, price=prop_price, city=db_city)
        prop_data = _prop_dict(prop)
        with patch('apps.ai.compliance_filters._load_property_by_id', return_value=prop_data), \
             patch('apps.ai.compliance_filters._reroute_to_agent'), \
             patch('apps.ai.compliance_filters._persist_violation'):
            return ComplianceEngine.check_output(
                reply, org=self.org, phone=self.user.phone,
                property_id=str(prop.id),
            )

    def test_arabic_dubai_detected(self):
        """Arabic 'دبي' (Dubai) should be detected as a city in LLM reply."""
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("هذا العقار يقع في دبي بسعر مليون درهم.")
        self.assertIsNotNone(claim.city, "Arabic city دبي should be extracted")

    def test_arabic_city_mismatch_blocked(self):
        """AI says Dubai (دبي) but property DB says Riyadh → location violation."""
        reply = "هذه الشقة تقع في دبي وسعرها مناسب."
        result = self._check(reply, db_city='Riyadh')
        self.assertFalse(result.safe)
        self.assertEqual(result.action_taken, 'blocked_location')

    def test_latin_dubai_matches_db_dubai(self):
        """AI says 'Dubai' (Latin) and DB says 'Dubai' → location passes."""
        reply = "This villa is located in Dubai near the marina."
        result = self._check(reply, db_city='Dubai')
        self.assertTrue(result.safe)

    def test_urdu_city_lahore_detected(self):
        """Urdu script 'لاہور' (Lahore) should be detected."""
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("یہ پراپرٹی لاہور میں واقع ہے۔")
        self.assertIsNotNone(claim.city, "Urdu city لاہور should be extracted")

    def test_russian_moscow_detected(self):
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("Эта квартира находится в Москве.")
        self.assertIsNotNone(claim.city, "Russian city Москва should be extracted")

    def test_chinese_shanghai_detected(self):
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("这套公寓位于上海。")
        self.assertIsNotNone(claim.city, "Chinese city 上海 should be extracted")

    def test_multilingual_installment_arabic_detected(self):
        """Arabic installment term 'بالتقسيط' should trigger installments_offered=True."""
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("يمكنك شراء هذا العقار بالتقسيط مع 20% دفعة أولى.")
        self.assertTrue(claim.installments_offered, "Arabic installment term not detected")

    def test_multilingual_installment_french_detected(self):
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("Vous pouvez acheter ce bien avec un paiement échelonné sur 5 ans.")
        self.assertTrue(claim.installments_offered, "French installment term not detected")

    def test_multilingual_installment_turkish_detected(self):
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("Bu mülkü taksitle satın alabilirsiniz.")
        self.assertTrue(claim.installments_offered, "Turkish installment term not detected")

    def test_multilingual_installment_chinese_detected(self):
        from apps.ai.compliance_filters import _extract_claims
        claim = _extract_claims("您可以分期付款购买这套公寓。")
        self.assertTrue(claim.installments_offered, "Chinese installment term not detected")
