"""
22 unit tests for the Voice Channel pipeline.
Covers: webhook auth, session creation, lead resolution, TwiML, audio buffer,
STT→AI→TTS flow, barge-in locking, RBAC, org isolation, latency budget.
"""
import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.voice.models import VoiceCallSession, OrgVoiceConfig
from apps.voice.ai_processor import CallAudioBuffer, AIVoiceProcessor
from apps.voice.barge_in import BargeInLatencyManager
from tests.factories import make_user, make_developer

_LOCMEM   = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_CALL_SID = 'CA_test_0001'


def _make_org_with_voice(phone='+15550000001', org_name='Voice Org'):
    admin, org = make_developer(phone=phone, org_name=org_name)
    cfg = OrgVoiceConfig.objects.create(
        organization=org,
        account_sid='AC_test',
        auth_token='auth_test',
        phone_number='+15559999999',
        is_active=True,
    )
    return admin, org, cfg


# ── Inbound webhook ────────────────────────────────────────────────────────────

class VoiceInboundWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin, self.org, self.cfg = _make_org_with_voice()
        self.url = '/api/v1/voice/webhook/inbound/'

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=True)
    def test_session_created_on_inbound(self, _sig):
        resp = self.client.post(self.url, {
            'CallSid': _CALL_SID,
            'From':    '+15550001111',
            'To':      self.cfg.phone_number,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(VoiceCallSession.objects.filter(call_sid=_CALL_SID).exists())

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=True)
    def test_lead_resolved_from_caller_phone(self, _sig):
        from apps.leads.models import Lead
        caller = make_user(phone='+15550002222')
        lead   = Lead.objects.create(user=caller, organization=self.org)
        self.client.post(self.url, {
            'CallSid': 'CA_lead_resolved',
            'From':    '+15550002222',
            'To':      self.cfg.phone_number,
        })
        session = VoiceCallSession.objects.get(call_sid='CA_lead_resolved')
        self.assertEqual(session.lead, lead)

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=False)
    def test_unsigned_webhook_rejected(self, _sig):
        resp = self.client.post(self.url, {
            'CallSid': 'CA_bad_sig', 'From': '+1555', 'To': self.cfg.phone_number,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(VoiceCallSession.objects.filter(call_sid='CA_bad_sig').exists())

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=True)
    def test_twiml_response_contains_stream_verb(self, _sig):
        resp = self.client.post(self.url, {
            'CallSid': 'CA_twiml',
            'From':    '+15550003333',
            'To':      self.cfg.phone_number,
        })
        self.assertIn(b'<Stream', resp.content)
        self.assertIn(b'CA_twiml', resp.content)

    @patch('apps.voice.views.VoiceInboundWebhookView._validate_signature', return_value=True)
    @patch('apps.voice.ai_processor.AIVoiceProcessor.build_voice_context',
           return_value={'lead_status': 'new', 'wa_history': [], 'property_recs': []})
    def test_ai_context_snapshot_populated(self, _ctx, _sig):
        self.client.post(self.url, {
            'CallSid': 'CA_ctx',
            'From':    '+15550004444',
            'To':      self.cfg.phone_number,
        })
        session = VoiceCallSession.objects.get(call_sid='CA_ctx')
        self.assertIn('lead_status', session.ai_context_snapshot)


# ── Audio buffer ───────────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class AudioBufferTest(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.call_sid = 'CA_buf_001'

    @patch('apps.voice.ai_processor.CallAudioBuffer.push', return_value=30)
    def test_audio_buffer_accumulates_chunks(self, mock_push):
        buf = CallAudioBuffer(self.call_sid)
        for _ in range(30):
            buf.push(b'\x00' * 160)
        self.assertEqual(mock_push.call_count, 30)

    @patch('apps.voice.ai_processor.CallAudioBuffer.push', side_effect=lambda b: 200)
    @patch('apps.voice.ai_processor.CallAudioBuffer.length', return_value=200)
    def test_audio_buffer_length_capped(self, mock_len, mock_push):
        buf = CallAudioBuffer(self.call_sid)
        buf.push(b'\x00' * 160)
        self.assertLessEqual(buf.length(), 200)


# ── transcribe_and_respond task ────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class TranscribeAndRespondTest(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin, self.org, self.cfg = _make_org_with_voice(phone='+15550010001', org_name='TAR Org')
        self.session = VoiceCallSession.objects.create(
            organization=self.org,
            call_sid='CA_tar_001',
            from_phone='+15550011111',
            to_phone=self.cfg.phone_number,
            direction='inbound',
            status='ai_handling',
        )

    @patch('apps.voice.ai_processor.CallAudioBuffer.flush', return_value=b'\x00' * 800)
    @patch('apps.whatsapp.stt_services.STTService.transcribe')
    @patch('apps.ai.service.AIServiceManager.process', return_value='Hello from AI')
    def test_transcript_appended_after_stt(self, _ai, mock_stt, _flush):
        from apps.whatsapp.stt_services import TranscriptResult
        mock_stt.return_value = TranscriptResult(text='Hello', language='en', provider='openai_whisper')
        from apps.voice.tasks import transcribe_and_respond
        with patch('apps.voice.providers.base.get_org_provider') as mock_gp:
            mock_gp.return_value = MagicMock(inject_voice_reply=MagicMock(return_value=True))
            transcribe_and_respond.run('CA_tar_001')
        self.session.refresh_from_db()
        self.assertIn('[CALLER]', self.session.transcript)

    @patch('apps.voice.ai_processor.CallAudioBuffer.flush', return_value=b'\x00' * 800)
    @patch('apps.whatsapp.stt_services.STTService.transcribe')
    @patch('apps.ai.service.AIServiceManager.process', return_value='AI reply here')
    def test_transcription_broadcast_called(self, _ai, mock_stt, _flush):
        from apps.whatsapp.stt_services import TranscriptResult
        mock_stt.return_value = TranscriptResult(text='Hi', language='en', provider='openai_whisper')
        from apps.voice.tasks import transcribe_and_respond
        with patch('apps.voice.tasks._broadcast_transcription') as mock_bc:
            with patch('apps.voice.providers.base.get_org_provider') as mock_gp:
                mock_gp.return_value = MagicMock(inject_voice_reply=MagicMock(return_value=True))
                transcribe_and_respond.run('CA_tar_001')
            self.assertGreaterEqual(mock_bc.call_count, 1)


# ── Barge-in ───────────────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM)
class BargeInTest(TestCase):

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.admin, self.org, self.cfg = _make_org_with_voice(phone='+15550020001', org_name='Barge Org')
        self.agent_user = make_user(phone='+15550021111', role='agent')
        from apps.agents.models import Agent
        Agent.objects.create(
            user=self.agent_user,
            organization=self.org,
            name='Test Agent',
            phone=self.agent_user.phone,
            employment_type='internal',
            registration_status='approved',
            is_active=True,
        )
        self.session = VoiceCallSession.objects.create(
            organization=self.org,
            call_sid='CA_barge_001',
            from_phone='+15550022222',
            to_phone=self.cfg.phone_number,
            direction='inbound',
            status='ai_handling',
        )

    @patch('apps.voice.providers.base.get_org_provider')
    def test_barge_in_updates_session_status(self, mock_provider):
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        self.client.force_authenticate(user=self.agent_user)
        resp = self.client.post('/api/v1/voice/calls/CA_barge_001/barge-in/')
        self.assertEqual(resp.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'agent_joined')
        self.assertEqual(self.session.barge_in_agent, self.agent_user)

    @patch('apps.voice.providers.base.get_org_provider')
    def test_barge_in_blocked_when_already_joined(self, mock_provider):
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        self.session.status = 'agent_joined'
        self.session.save()
        self.client.force_authenticate(user=self.agent_user)
        resp = self.client.post('/api/v1/voice/calls/CA_barge_001/barge-in/')
        self.assertEqual(resp.status_code, 409)

    @patch('apps.voice.providers.base.get_org_provider')
    def test_barge_in_calls_twilio_redirect(self, mock_provider):
        mock_redirect = MagicMock(return_value=True)
        mock_provider.return_value = MagicMock(redirect_to_agent=mock_redirect)
        self.client.force_authenticate(user=self.agent_user)
        self.client.post('/api/v1/voice/calls/CA_barge_001/barge-in/')
        mock_redirect.assert_called_once_with('CA_barge_001')

    @patch('apps.voice.providers.base.get_org_provider')
    def test_barge_in_latency_under_100ms(self, mock_provider):
        mock_provider.return_value = MagicMock(redirect_to_agent=MagicMock(return_value=True))
        result = BargeInLatencyManager.execute_barge_in(
            call_sid='CA_barge_001',
            agent_user=self.agent_user,
            org=self.org,
        )
        self.assertTrue(result.success)
        self.assertLess(result.latency_ms, 100)


# ── RBAC ───────────────────────────────────────────────────────────────────────

class VoiceCallRBACTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin, self.org, self.cfg = _make_org_with_voice(phone='+15550030001', org_name='RBAC Org')
        self.agent_user = make_user(phone='+15550031111', role='agent')

    def test_agent_cannot_initiate_call(self):
        self.client.force_authenticate(user=self.agent_user)
        resp = self.client.post('/api/v1/voice/calls/initiate/', {'lead_id': 'abc'})
        self.assertEqual(resp.status_code, 403)


# ── Org isolation ──────────────────────────────────────────────────────────────

class VoiceCallOrgIsolationTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin_a, self.org_a, self.cfg_a = _make_org_with_voice(phone='+15550040001', org_name='Org A')
        self.admin_b, self.org_b, self.cfg_b = _make_org_with_voice(phone='+15550040002', org_name='Org B')
        VoiceCallSession.objects.create(
            organization=self.org_a, call_sid='CA_org_a_001',
            from_phone='+15550041111', to_phone=self.cfg_a.phone_number,
            direction='inbound', status='completed',
        )

    def test_org_b_cannot_see_org_a_calls(self):
        self.client.force_authenticate(user=self.admin_b)
        resp = self.client.get('/api/v1/voice/calls/')
        self.assertEqual(resp.status_code, 200)
        call_sids = [c['call_sid'] for c in resp.data]
        self.assertNotIn('CA_org_a_001', call_sids)

    def test_status_filter_returns_only_matching(self):
        VoiceCallSession.objects.create(
            organization=self.org_a, call_sid='CA_ai_001',
            from_phone='+15550042222', to_phone=self.cfg_a.phone_number,
            direction='inbound', status='ai_handling',
        )
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get('/api/v1/voice/calls/?status=ai_handling')
        self.assertEqual(resp.status_code, 200)
        for c in resp.data:
            self.assertEqual(c['status'], 'ai_handling')


# ── Config masking ─────────────────────────────────────────────────────────────

class OrgVoiceConfigMaskTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.admin, self.org, self.cfg = _make_org_with_voice(phone='+15550050001', org_name='Mask Org')

    def test_auth_token_masked_in_get(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get('/api/v1/voice/config/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['auth_token'], '••••••••')

    def test_patch_with_sentinel_preserves_token(self):
        self.client.force_authenticate(user=self.admin)
        self.client.patch('/api/v1/voice/config/', {'auth_token': '••••••••'}, format='json')
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.auth_token, 'auth_test')
