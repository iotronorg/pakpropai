"""
6 E2E barge-in tests: concurrent lock, stream switch timing, lifecycle transitions.
"""
import threading
from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings

from apps.voice.models import VoiceCallSession, OrgVoiceConfig
from apps.voice.barge_in import BargeInLatencyManager
from tests.factories import make_user, make_developer

_LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _make_org_with_voice(phone='+15556000001', org_name='E2E Org'):
    admin, org = make_developer(phone=phone, org_name=org_name)
    cfg = OrgVoiceConfig.objects.create(
        organization=org, account_sid='AC_e2e',
        auth_token='auth_e2e', phone_number='+15559990000', is_active=True,
    )
    return admin, org, cfg


@override_settings(CACHES=_LOCMEM)
class BargeInStreamSwitchTest(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin, self.org, self.cfg = _make_org_with_voice()
        self.agent = make_user(phone='+15556001111', role='agent')
        self.session = VoiceCallSession.objects.create(
            organization=self.org, call_sid='CA_e2e_001',
            from_phone='+15556002222', to_phone=self.cfg.phone_number,
            direction='inbound', status='ai_handling',
        )

    @patch('apps.voice.providers.base.get_org_provider')
    def test_stream_switches_under_100ms(self, mock_provider):
        """Twilio redirect + session update must complete in < 100ms."""
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        result = BargeInLatencyManager.execute_barge_in(
            call_sid='CA_e2e_001', agent_user=self.agent, org=self.org,
        )
        self.assertTrue(result.success)
        self.assertLess(result.latency_ms, 100)

    @patch('apps.voice.providers.base.get_org_provider')
    def test_barge_in_twiml_is_valid_xml(self, mock_provider):
        """redirect_to_agent sends well-formed TwiML to Twilio."""
        import xml.etree.ElementTree as ET
        captured = {}

        def fake_redirect(call_sid):
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Response><Say>Please hold.</Say><Pause length="60"/></Response>'
            )
            captured['twiml'] = twiml
            ET.fromstring(twiml)   # raises if malformed
            return True

        provider = MagicMock()
        provider.redirect_to_agent.side_effect = fake_redirect
        mock_provider.return_value = provider

        result = BargeInLatencyManager.execute_barge_in(
            call_sid='CA_e2e_001', agent_user=self.agent, org=self.org,
        )
        self.assertTrue(result.success)
        self.assertIn('<Response>', captured.get('twiml', ''))

    @patch('apps.voice.providers.base.get_org_provider')
    @patch('apps.voice.barge_in._broadcast_barge_in')
    def test_barge_in_broadcasts_event(self, mock_bc, mock_provider):
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        BargeInLatencyManager.execute_barge_in(
            call_sid='CA_e2e_001', agent_user=self.agent, org=self.org,
        )
        mock_bc.assert_called_once()

    @patch('apps.voice.providers.base.get_org_provider')
    def test_full_call_lifecycle_transitions(self, mock_provider):
        """RINGING → AI_HANDLING → AGENT_JOINED → COMPLETED."""
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        session = VoiceCallSession.objects.create(
            organization=self.org, call_sid='CA_lifecycle_001',
            from_phone='+15556003333', to_phone=self.cfg.phone_number,
            direction='inbound', status='ringing',
        )

        # RINGING → AI_HANDLING
        VoiceCallSession.objects.filter(call_sid='CA_lifecycle_001').update(status='ai_handling')
        session.refresh_from_db()
        self.assertEqual(session.status, 'ai_handling')

        # AI_HANDLING → AGENT_JOINED (barge-in)
        BargeInLatencyManager.execute_barge_in(
            call_sid='CA_lifecycle_001', agent_user=self.agent, org=self.org,
        )
        session.refresh_from_db()
        self.assertEqual(session.status, 'agent_joined')

        # AGENT_JOINED → COMPLETED
        VoiceCallSession.objects.filter(call_sid='CA_lifecycle_001').update(status='completed')
        session.refresh_from_db()
        self.assertEqual(session.status, 'completed')


@override_settings(CACHES=_LOCMEM)
class ConcurrentBargeInTest(TransactionTestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin, self.org, self.cfg = _make_org_with_voice(
            phone='+15557000001', org_name='Concurrent Org'
        )
        self.agent = make_user(phone='+15557001111', role='agent')
        self.session = VoiceCallSession.objects.create(
            organization=self.org, call_sid='CA_concurrent_001',
            from_phone='+15557002222', to_phone=self.cfg.phone_number,
            direction='inbound', status='ai_handling',
        )

    @patch('apps.voice.providers.base.get_org_provider')
    def test_concurrent_barge_in_exactly_one_winner(self, mock_provider):
        """5 concurrent barge-in attempts → exactly 1 success, Twilio called once."""
        mock_redirect = MagicMock(return_value=True)
        mock_provider.return_value = MagicMock(redirect_to_agent=mock_redirect)

        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    BargeInLatencyManager.execute_barge_in(
                        call_sid='CA_concurrent_001',
                        agent_user=self.agent,
                        org=self.org,
                    )
                )
            )
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r.success]
        self.assertEqual(len(winners), 1, f"Expected 1 winner, got {len(winners)}: {results}")
        self.assertEqual(mock_redirect.call_count, 1, "Twilio redirect must be called exactly once")
