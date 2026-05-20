"""
Media pipeline tests — WhatsAppMediaDownloader, STTService, Celery tasks,
webhook dispatch, and multi-tenant isolation.

Run: python manage.py test tests.test_media_pipelines --settings=config.settings.test

Patch note: service classes are lazy-imported inside task bodies.
Patch at the SOURCE module, not 'apps.whatsapp.tasks.<class>'.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.whatsapp.media_services import (
    MediaAuthError, MediaCorruptedError, MediaDownloadResult,
    MediaNotFoundError, MediaRateLimitError, MediaSizeError,
    WhatsAppMediaDownloader,
)
from apps.whatsapp.stt_services import STTService, TranscriptResult


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve(url='https://cdn.whatsapp.net/abc'):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {'url': url}
    r.raise_for_status = MagicMock()
    return r

def _binary(data=b'FAKE_OGG'):
    r = MagicMock()
    r.status_code = 200
    r.iter_content = MagicMock(return_value=iter([data]))
    r.raise_for_status = MagicMock()
    return r

def _err(code):
    r = MagicMock()
    r.status_code = code
    r.text = f"HTTP {code}"
    r.raise_for_status = MagicMock()
    return r

def _dl_result(**kw):
    defaults = dict(data=b'OGG', mime_type='audio/ogg', size_bytes=3,
                    cdn_url='', media_id='m1')
    defaults.update(kw)
    return MediaDownloadResult(**defaults)

def _stt(text='hello', lang='en', provider='openai_whisper'):
    return TranscriptResult(text=text, language=lang, provider=provider)


# ── 1. WhatsAppMediaDownloader ─────────────────────────────────────────────────

class TestWhatsAppMediaDownloader(TestCase):

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_happy_path(self, _tok, mock_get):
        mock_get.side_effect = [_resolve(), _binary(b'OGG_BYTES')]
        r = WhatsAppMediaDownloader.download('m1', 'audio/ogg')
        self.assertEqual(r.data, b'OGG_BYTES')
        self.assertEqual(r.mime_type, 'audio/ogg')
        self.assertEqual(r.size_bytes, len(b'OGG_BYTES'))
        self.assertEqual(r.cdn_url, '')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_401_raises_auth_error(self, _tok, mock_get):
        mock_get.return_value = _err(401)
        with self.assertRaises(MediaAuthError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_403_raises_auth_error(self, _tok, mock_get):
        mock_get.return_value = _err(403)
        with self.assertRaises(MediaAuthError):
            WhatsAppMediaDownloader.download('m1', 'image/jpeg')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_404_raises_not_found(self, _tok, mock_get):
        mock_get.return_value = _err(404)
        with self.assertRaises(MediaNotFoundError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_429_raises_rate_limit(self, _tok, mock_get):
        mock_get.return_value = _err(429)
        with self.assertRaises(MediaRateLimitError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_empty_payload_raises_corrupted(self, _tok, mock_get):
        mock_get.side_effect = [_resolve(), _binary(b'')]
        with self.assertRaises(MediaCorruptedError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_unsupported_mime_raises_corrupted(self, _tok, mock_get):
        mock_get.side_effect = [_resolve(), _binary(b'VIDEO')]
        with self.assertRaises(MediaCorruptedError):
            WhatsAppMediaDownloader.download('m1', 'video/mp4')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_oversized_payload_raises_size_error(self, _tok, mock_get):
        over = b'X' * (16 * 1024 * 1024 + 1)
        b = MagicMock()
        b.status_code = 200
        b.raise_for_status = MagicMock()
        b.iter_content = MagicMock(return_value=iter([over]))
        mock_get.side_effect = [_resolve(), b]
        with self.assertRaises(MediaSizeError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')

    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._upload_to_cloudinary')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._cloudinary_configured', return_value=True)
    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_cloudinary_upload_called_and_url_returned(self, _tok, mock_get, _cfg, mock_upload):
        mock_get.side_effect = [_resolve(), _binary(b'AUDIO')]
        mock_upload.return_value = 'https://res.cloudinary.com/demo/video/upload/wa-media/m1.ogg'
        r = WhatsAppMediaDownloader.download('m1', 'audio/ogg')
        mock_upload.assert_called_once()
        self.assertEqual(r.cdn_url, 'https://res.cloudinary.com/demo/video/upload/wa-media/m1.ogg')

    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._upload_to_cloudinary',
           side_effect=Exception("Cloudinary timeout"))
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._cloudinary_configured', return_value=True)
    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_cloudinary_failure_non_fatal(self, _tok, mock_get, _cfg, _upload):
        mock_get.side_effect = [_resolve(), _binary(b'AUDIO')]
        r = WhatsAppMediaDownloader.download('m1', 'audio/ogg')
        self.assertEqual(r.data, b'AUDIO')
        self.assertEqual(r.cdn_url, '')

    @patch('apps.whatsapp.media_services.requests.get')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader._token', return_value='tok')
    def test_401_on_binary_step_raises_auth(self, _tok, mock_get):
        mock_get.side_effect = [_resolve(), _err(401)]
        with self.assertRaises(MediaAuthError):
            WhatsAppMediaDownloader.download('m1', 'audio/ogg')


# ── 2. STTService ──────────────────────────────────────────────────────────────

class TestSTTService(TestCase):

    def test_empty_bytes_returns_none_provider(self):
        r = STTService.transcribe(b'', 'audio/ogg')
        self.assertEqual(r.provider, 'none')
        self.assertEqual(r.text, '')

    @patch.object(STTService, '_try_openai_whisper',
                  return_value=_stt('5 marla Lahore', 'ur', 'openai_whisper'))
    def test_openai_result_returned_directly(self, _):
        r = STTService.transcribe(b'AUDIO', 'audio/ogg')
        self.assertEqual(r.provider, 'openai_whisper')
        self.assertEqual(r.language, 'ur')

    @patch.object(STTService, '_try_gemini',
                  return_value=_stt('DHA plot chahiye', '', 'gemini'))
    @patch.object(STTService, '_try_openai_whisper', return_value=None)
    def test_falls_back_to_gemini(self, _w, _g):
        r = STTService.transcribe(b'AUDIO', 'audio/ogg')
        self.assertEqual(r.provider, 'gemini')

    @patch.object(STTService, '_try_gemini', return_value=None)
    @patch.object(STTService, '_try_openai_whisper', return_value=None)
    def test_all_fail_returns_empty(self, _w, _g):
        r = STTService.transcribe(b'CORRUPT', 'audio/ogg')
        self.assertEqual(r.text, '')
        self.assertEqual(r.provider, 'none')

    @patch('apps.config.services.SystemConfigService.get', return_value='sk-key')
    def test_rate_limit_handled(self, _cfg):
        import openai
        with patch('openai.OpenAI') as cls:
            cls.return_value.audio.transcriptions.create.side_effect = (
                openai.RateLimitError(message='limit', response=MagicMock(), body={})
            )
            self.assertIsNone(STTService._try_openai_whisper(b'AUDIO', 'audio/ogg'))

    @patch('apps.config.services.SystemConfigService.get', return_value='sk-key')
    def test_corrupted_audio_bad_request_handled(self, _cfg):
        import openai
        with patch('openai.OpenAI') as cls:
            cls.return_value.audio.transcriptions.create.side_effect = (
                openai.BadRequestError(message='bad', response=MagicMock(), body={})
            )
            self.assertIsNone(STTService._try_openai_whisper(b'\x00', 'audio/ogg'))

    @override_settings(OPENAI_API_KEY='')
    @patch('apps.config.services.SystemConfigService.get', return_value='')
    def test_openai_skipped_without_key(self, _cfg):
        self.assertIsNone(STTService._try_openai_whisper(b'AUDIO', 'audio/ogg'))

    @override_settings(GEMINI_API_KEY='')
    @patch('apps.config.services.SystemConfigService.get', return_value='')
    def test_gemini_skipped_without_key(self, _cfg):
        self.assertIsNone(STTService._try_gemini(b'AUDIO', 'audio/ogg'))

    @patch('apps.config.services.SystemConfigService.get', return_value='bad-key')
    def test_auth_error_handled(self, _cfg):
        import openai
        with patch('openai.OpenAI') as cls:
            cls.return_value.audio.transcriptions.create.side_effect = (
                openai.AuthenticationError(message='bad', response=MagicMock(), body={})
            )
            self.assertIsNone(STTService._try_openai_whisper(b'AUDIO', 'audio/ogg'))


# ── 3. transcribe_audio_task (unit) ───────────────────────────────────────────

class TestTranscribeAudioTask(TestCase):

    def _msg(self, mid='m_voice'):
        return {'id': 'wa_001', 'from': '923001234567', 'type': 'audio',
                'audio': {'id': mid, 'mime_type': 'audio/ogg'}}

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.stt_services.STTService.transcribe')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_transcript_injected_and_router_called(self, mock_dl, mock_stt, mock_route):
        """Core assertion: transcript lands in message_data before routing."""
        mock_dl.return_value = _dl_result(
            data=b'OGG', cdn_url='https://res.cloudinary.com/demo/video/upload/wa-media/m_voice.ogg'
        )
        mock_stt.return_value = _stt('5 marla DHA Lahore', 'ur')
        msg = self._msg()
        from apps.whatsapp.tasks import transcribe_audio_task
        transcribe_audio_task('m_voice', 'audio/ogg', '923001234567', '', msg)

        self.assertEqual(msg['audio']['_transcript'], '5 marla DHA Lahore')
        self.assertEqual(msg['audio']['_cdn_url'], 'https://res.cloudinary.com/demo/video/upload/wa-media/m_voice.ogg')
        mock_route.assert_called_once_with(msg, '923001234567', '')

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_corrupted_download_still_routes(self, mock_dl, mock_route):
        """Download failure → empty transcript injected → router still called."""
        mock_dl.side_effect = MediaCorruptedError("empty payload")
        msg = self._msg('m_corrupt')
        from apps.whatsapp.tasks import transcribe_audio_task
        transcribe_audio_task('m_corrupt', 'audio/ogg', '923001234567', '', msg)
        mock_route.assert_called_once()
        self.assertEqual(msg['audio']['_transcript'], '')

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.stt_services.STTService.transcribe')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_no_cdn_url_not_injected(self, mock_dl, mock_stt, mock_route):
        mock_dl.return_value = _dl_result(cdn_url='')
        mock_stt.return_value = _stt()
        msg = self._msg()
        from apps.whatsapp.tasks import transcribe_audio_task
        transcribe_audio_task('m1', 'audio/ogg', '923001234567', '', msg)
        self.assertNotIn('_cdn_url', msg['audio'])

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.stt_services.STTService.transcribe')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_empty_transcript_still_routes(self, mock_dl, mock_stt, mock_route):
        mock_dl.return_value = _dl_result()
        mock_stt.return_value = _stt('', provider='none')
        from apps.whatsapp.tasks import transcribe_audio_task
        transcribe_audio_task('m1', 'audio/ogg', '923001234567', '', self._msg())
        mock_route.assert_called_once()


# ── 4. process_image_task / process_document_task ─────────────────────────────

class TestProcessMediaTasks(TestCase):

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_image_task_injects_cdn_url(self, mock_dl, mock_route):
        mock_dl.return_value = _dl_result(
            mime_type='image/jpeg',
            cdn_url='https://res.cloudinary.com/demo/image/upload/wa-media/m_img.jpg',
        )
        msg = {'id': 'wa_002', 'from': '923001234567', 'type': 'image',
               'image': {'id': 'm_img', 'mime_type': 'image/jpeg', 'caption': ''}}
        from apps.whatsapp.tasks import process_image_task
        process_image_task('m_img', 'image/jpeg', '923001234567', '', '', msg)
        self.assertEqual(msg['image']['_cdn_url'], 'https://res.cloudinary.com/demo/image/upload/wa-media/m_img.jpg')
        mock_route.assert_called_once()

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_unsupported_image_format_does_not_crash(self, mock_dl, mock_route):
        mock_dl.side_effect = MediaCorruptedError("video/mp4 not supported")
        msg = {'id': 'wa_003', 'from': '923001234567', 'type': 'image',
               'image': {'id': 'm_vid', 'mime_type': 'video/mp4'}}
        from apps.whatsapp.tasks import process_image_task
        process_image_task('m_vid', 'video/mp4', '923001234567', '', '', msg)
        mock_route.assert_called_once()

    @patch('apps.whatsapp.tasks._safe_route')
    @patch('apps.whatsapp.media_services.WhatsAppMediaDownloader.download')
    def test_document_task_routes_on_success(self, mock_dl, mock_route):
        mock_dl.return_value = _dl_result(mime_type='application/pdf',
                                          cdn_url='https://res.cloudinary.com/demo/raw/upload/wa-media/m_doc.pdf')
        msg = {'id': 'wa_004', 'from': '923001234567', 'type': 'document',
               'document': {'id': 'm_doc', 'mime_type': 'application/pdf',
                            'filename': 'allotment.pdf', 'caption': 'verify'}}
        from apps.whatsapp.tasks import process_document_task
        process_document_task('m_doc', 'application/pdf', '923001234567', '',
                              'allotment.pdf', 'verify', msg)
        mock_route.assert_called_once()


# ── 5. Webhook → dispatch ──────────────────────────────────────────────────────

class TestWebhookDispatch(TestCase):

    @patch('apps.whatsapp.tasks.transcribe_audio_task')
    @patch('apps.whatsapp.client.WhatsAppClient.mark_read')
    def test_audio_dispatches_transcribe_task(self, _mr, mock_task):
        msg = {'id': 'wa_001', 'from': '923001234567', 'type': 'audio',
               'audio': {'id': 'm_audio', 'mime_type': 'audio/ogg'}}
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        process_incoming_whatsapp_task(msg, 'pid1')
        mock_task.delay.assert_called_once_with('m_audio', 'audio/ogg', '923001234567', 'pid1', msg)

    @patch('apps.whatsapp.tasks.process_image_task')
    @patch('apps.whatsapp.client.WhatsAppClient.mark_read')
    def test_image_dispatches_image_task(self, _mr, mock_task):
        msg = {'id': 'wa_002', 'from': '923001234567', 'type': 'image',
               'image': {'id': 'm_img', 'mime_type': 'image/jpeg', 'caption': ''}}
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        process_incoming_whatsapp_task(msg, 'pid1')
        mock_task.delay.assert_called_once_with('m_img', 'image/jpeg', '923001234567', 'pid1', '', msg)

    @patch('apps.whatsapp.router.MessageRouter.route')
    @patch('apps.whatsapp.client.WhatsAppClient.mark_read')
    def test_text_routes_directly_no_media_worker(self, _mr, mock_route):
        msg = {'id': 'wa_003', 'from': '923001234567', 'type': 'text',
               'text': {'body': 'I want to buy a house'}}
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        process_incoming_whatsapp_task(msg, 'pid1')
        mock_route.assert_called_once_with(msg, '923001234567', 'pid1')

    @patch('apps.whatsapp.client.WhatsAppClient.mark_read')
    def test_missing_id_dropped_silently(self, mock_mr):
        from apps.whatsapp.tasks import process_incoming_whatsapp_task
        process_incoming_whatsapp_task({'from': '923001234567'}, '')
        mock_mr.assert_not_called()


# ── 6. Router pre-fetched transcript fast-path ────────────────────────────────

class TestRouterTranscriptFastPath(TestCase):

    def test_pre_fetched_transcript_skips_download(self):
        from apps.whatsapp.router import MessageRouter
        msg = {'type': 'audio',
               'audio': {'id': 'mid', 'mime_type': 'audio/ogg',
                         '_transcript': 'DHA 5 marla chahiye'}}
        with patch('apps.whatsapp.client.WhatsAppClient.download_media') as mock_dl:
            result = MessageRouter._transcribe_voice(msg, '923001234567')
        self.assertEqual(result, 'DHA 5 marla chahiye')
        mock_dl.assert_not_called()

    def test_empty_pre_fetched_transcript_returned_without_fallback(self):
        from apps.whatsapp.router import MessageRouter
        msg = {'type': 'audio',
               'audio': {'id': 'mid', 'mime_type': 'audio/ogg', '_transcript': ''}}
        with patch('apps.whatsapp.client.WhatsAppClient.download_media') as mock_dl:
            result = MessageRouter._transcribe_voice(msg, '923001234567')
        self.assertEqual(result, '')
        mock_dl.assert_not_called()


# ── 7. Multi-tenant isolation regression check ────────────────────────────────

class TestMultiTenantIsolation(TestCase):
    """
    Baseline regression: Phase 1 multi-tenant DB rules are not bypassed when
    media transcripts are saved to the WhatsApp message ledger.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
        from apps.organizations.models import Organization
        from apps.leads.models import Lead

        User = get_user_model()

        self.admin_a = User.objects.create_user(phone='+923001111111', role='developer', is_active=True)
        self.org_a   = Organization.objects.create(name='Org Alpha', slug='org-alpha', admin_user=self.admin_a)
        self.user_a  = User.objects.create_user(phone='+923009991111', role='client', is_active=True)
        self.sess_a  = WhatsAppSession.objects.create(phone='923009991111', user=self.user_a)
        self.lead_a  = Lead.objects.create(user=self.user_a, organization=self.org_a)
        self.msg_a   = WhatsAppMessage.objects.create(
            session=self.sess_a, wa_message_id='wa_a_001',
            direction='inbound', msg_type='audio',
            body='[voice] DHA Lahore 5 marla under 2 crore', media_id='media_a_001',
        )

        self.admin_b = User.objects.create_user(phone='+923002222222', role='developer', is_active=True)
        self.org_b   = Organization.objects.create(name='Org Beta', slug='org-beta', admin_user=self.admin_b)
        self.user_b  = User.objects.create_user(phone='+923009992222', role='client', is_active=True)
        self.sess_b  = WhatsAppSession.objects.create(phone='923009992222', user=self.user_b)
        self.lead_b  = Lead.objects.create(user=self.user_b, organization=self.org_b)
        self.msg_b   = WhatsAppMessage.objects.create(
            session=self.sess_b, wa_message_id='wa_b_001',
            direction='inbound', msg_type='image',
            body='[image] allotment letter', media_id='media_b_001',
        )

    def test_message_scoped_to_own_session(self):
        from apps.whatsapp.models import WhatsAppMessage
        qs_a = WhatsAppMessage.objects.filter(session=self.sess_a)
        qs_b = WhatsAppMessage.objects.filter(session=self.sess_b)
        self.assertIn(self.msg_a, qs_a)
        self.assertNotIn(self.msg_b, qs_a)
        self.assertIn(self.msg_b, qs_b)
        self.assertNotIn(self.msg_a, qs_b)

    def test_org_lead_phones_exclude_other_org(self):
        from apps.leads.models import Lead
        phones_a = set(Lead.objects.filter(organization=self.org_a).values_list('user__phone', flat=True))
        self.assertIn('+923009991111', phones_a)
        self.assertNotIn('+923009992222', phones_a)

    def test_session_query_scoped_excludes_other_org(self):
        from apps.leads.models import Lead
        from apps.whatsapp.models import WhatsAppSession
        phones = [p.lstrip('+') for p in Lead.objects.filter(organization=self.org_a).values_list('user__phone', flat=True)]
        sessions = WhatsAppSession.objects.filter(phone__in=phones)
        self.assertIn(self.sess_a, sessions)
        self.assertNotIn(self.sess_b, sessions)

    def test_cross_tenant_message_query_isolation(self):
        from apps.leads.models import Lead
        from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
        phones   = [p.lstrip('+') for p in Lead.objects.filter(organization=self.org_a).values_list('user__phone', flat=True)]
        sessions = WhatsAppSession.objects.filter(phone__in=phones)
        ids      = list(WhatsAppMessage.objects.filter(session__in=sessions).values_list('id', flat=True))
        self.assertIn(self.msg_a.id, ids)
        self.assertNotIn(self.msg_b.id, ids)

    def test_transcript_body_persisted_verbatim(self):
        from apps.whatsapp.models import WhatsAppMessage
        saved = WhatsAppMessage.objects.get(pk=self.msg_a.pk)
        self.assertEqual(saved.body, '[voice] DHA Lahore 5 marla under 2 crore')
        self.assertEqual(saved.msg_type, 'audio')
        self.assertEqual(saved.media_id, 'media_a_001')
