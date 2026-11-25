"""
T-2070 — AI kill switch per platform, market and organization (ADR-043).

A stop ends the turn before any model, tool or confirmation; every model call
in ``apps.ai.llm`` refuses; direct provider calls (OCR, speech to text) are
skipped; drafts are empty; the API is limited to platform AI/ops admins and
audited.
"""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation, kill_switch, llm, tool_registry as R
from apps.ai._tools_context import set_context
from apps.ai.models import AIKillSwitch, AITrace, PendingAction
from apps.ai.service import AIServiceManager
from apps.core.models import AuditLog
from apps.markets.models import Market
from apps.organizations.models import Organization

User = get_user_model()
PHONE = '923001230001'


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Stop Org', country='PK', is_active=True)
        self.other = Organization.objects.create(name='Dubai Org', country='AE', is_active=True)
        self.pk = Market.objects.get(pk='PK')
        self.user = User.objects.create(phone='+' + PHONE, role='client')

    def svc(self):
        svc = AIServiceManager()
        svc._agent = MagicMock()
        svc._agent._load_history.return_value = []
        svc._agent.chat.return_value = 'Here is an answer for you.'
        return svc


class StateTest(Base):
    def test_scopes(self):
        self.assertIsNone(kill_switch.active(self.org))
        s = kill_switch.stop(reason='x', organization=self.org)
        self.assertTrue(kill_switch.is_stopped(self.org))
        self.assertFalse(kill_switch.is_stopped(self.other))
        self.assertFalse(kill_switch.is_stopped())
        kill_switch.release(s)
        self.assertFalse(kill_switch.is_stopped(self.org))

        s = kill_switch.stop(reason='x', market=self.pk)
        self.assertTrue(kill_switch.is_stopped(self.org))
        self.assertFalse(kill_switch.is_stopped(self.other))
        self.assertTrue(kill_switch.is_stopped(market='pk'))
        self.assertTrue(kill_switch.is_stopped())                        # no org: the default market
        kill_switch.release(s)

        kill_switch.stop(reason='x')
        self.assertTrue(kill_switch.is_stopped(self.other))

    def test_scope_applies_to_calls_without_their_own_and_is_restored(self):
        kill_switch.stop(reason='x', organization=self.org)
        with kill_switch.scope(self.org):
            self.assertTrue(kill_switch.is_stopped())
            self.assertTrue(kill_switch.is_stopped(market='AE'))
            self.assertFalse(kill_switch.is_stopped(self.other))         # an explicit target wins
        self.assertFalse(kill_switch.is_stopped())

    def test_one_stop_per_target_and_shape(self):
        s = kill_switch.stop(reason='x', market=self.pk)
        with self.assertRaises(kill_switch.AlreadyStopped):
            kill_switch.stop(reason='again', market=self.pk)
        kill_switch.stop(reason='platform')
        with self.assertRaises(kill_switch.AlreadyStopped):
            kill_switch.stop(reason='platform again')
        kill_switch.release(s)
        with self.assertRaises(kill_switch.AlreadyReleased):
            kill_switch.release(s)
        kill_switch.stop(reason='x', market=self.pk)                     # history does not block a new stop
        with self.assertRaises(IntegrityError), transaction.atomic():
            AIKillSwitch.objects.create(scope='market', reason='no market')


class TurnTest(Base):
    def test_turn_ends_before_the_model_and_is_traced(self):
        kill_switch.stop(reason='x', organization=self.org)
        svc = self.svc()
        reply = svc.process(PHONE, 'flats in Lahore please', self.user, organization=self.org)
        self.assertEqual(reply, kill_switch.paused_reply('flats in Lahore please'))
        svc._agent.chat.assert_not_called()
        trace = AITrace.objects.get()
        self.assertEqual((trace.route, trace.llm_calls), (AITrace.Route.STOPPED, 0))
        # the notice does not enter the model's window
        from apps.conversations import store
        self.assertEqual(store.window(store.find(PHONE, self.org)), [])

    def test_other_organizations_are_unaffected_and_release_restores(self):
        s = kill_switch.stop(reason='x', organization=self.org)
        self.assertEqual(self.svc().process(PHONE, 'hello there friend', self.user, organization=self.other),
                         'Here is an answer for you.')
        kill_switch.release(s)
        self.assertEqual(self.svc().process(PHONE, 'hello there friend', self.user, organization=self.org),
                         'Here is an answer for you.')

    def test_draft_is_empty(self):
        kill_switch.stop(reason='x', organization=self.org)
        self.assertEqual(self.svc().process(PHONE, 'suggest a reply', organization=self.org, interactive=False), '')

    def test_web_conversation_market_is_covered(self):
        from apps.conversations import store
        conv = store.open_web(self.user)
        conv.market_code = 'AE'
        conv.save()
        kill_switch.stop(reason='x', market=Market.objects.get(pk='AE'))
        msg, _ = store.begin_turn(conv, 'hello there friend')
        reply = self.svc().process(PHONE, msg.content, self.user, conversation=conv, user_message=msg)
        self.assertEqual(reply, kill_switch.paused_reply())

    def test_reply_language(self):
        self.assertIn('abhi band hai', kill_switch.paused_reply('mujhe lahore mein ghar chahiye'))
        self.assertIn('اسسٹنٹ', kill_switch.paused_reply('مجھے لاہور میں گھر چاہیے'))
        self.assertIn('المساعد', kill_switch.paused_reply('أريد شقة'))
        self.assertIn('paused', kill_switch.paused_reply('a flat in Lahore'))


class ConfirmationTest(Base):
    def propose(self):
        set_context(self.user, PHONE, org=self.org, message='connect me')
        confirmation.propose(R.REGISTRY['connect_to_agent'], {'city': 'Lahore'})
        set_context(None, '')
        return PendingAction.objects.get()

    def test_yes_does_nothing_and_cannot_fire_later(self):
        action = self.propose()
        s = kill_switch.stop(reason='x', organization=self.org)
        with patch('apps.ai.tools.connect_to_agent') as tool:
            svc = self.svc()
            self.assertEqual(svc.process(PHONE, 'yes', self.user, organization=self.org),
                             kill_switch.paused_reply('yes'))
            action.refresh_from_db()
            self.assertEqual((action.status, action.awaiting_reply), ('pending', False))
            kill_switch.release(s)
            svc.process(PHONE, 'yes', self.user, organization=self.org)
            tool.assert_not_called()

    def test_api_confirm_is_refused_but_decline_works(self):
        action = self.propose()
        kill_switch.stop(reason='x', organization=self.org)
        api = APIClient()
        api.force_authenticate(self.user)
        with patch('apps.ai.tools.connect_to_agent') as tool:
            r = api.post(f'/api/v1/pending-actions/{action.pk}/confirm/')
            self.assertEqual(r.status_code, 409, r.content)
            tool.assert_not_called()
        action.refresh_from_db()
        self.assertEqual(action.status, 'pending')
        self.assertEqual(api.post(f'/api/v1/pending-actions/{action.pk}/decline/').status_code, 200)


class ModelCallTest(Base):
    def test_every_router_call_refuses(self):
        kill_switch.stop(reason='x', market=self.pk)
        with patch('apps.ai.llm._candidates') as providers:
            for call in (lambda: llm.complete('fraud.check', {'query': 'x'}, organization=self.org),
                         lambda: llm.complete_text('hi'),
                         lambda: llm.vision('document.ocr', b'x', 'image/png', country='PK'),
                         lambda: llm.chat('hi', [], [], 'sys')):
                with self.assertRaises(llm.AIStopped):
                    call()
            providers.assert_not_called()

    def test_cached_answer_is_not_served(self):
        backend = MagicMock()
        backend.complete.return_value = MagicMock(text='cached text', model='m', prompt_tokens=1, completion_tokens=1)
        with patch('apps.ai.llm._candidates', return_value=[('gemini', backend)]):
            self.assertEqual(llm.complete_text('hi', cache_ttl=60).text, 'cached text')
            kill_switch.stop(reason='x')
            with self.assertRaises(llm.AIStopped):
                llm.complete_text('hi', cache_ttl=60)

    def test_direct_provider_calls_are_skipped(self):
        from apps.ai.agent import RealTronAgent
        from apps.whatsapp.stt_services import STTService
        kill_switch.stop(reason='x', organization=self.org)
        agent = RealTronAgent()
        with patch.object(RealTronAgent, '_get_backend') as backend, \
                patch.object(STTService, '_try_openai_whisper') as whisper, \
                patch.object(STTService, '_try_gemini') as gemini:
            self.assertEqual(agent.verify_document_image(PHONE, b'x', 'image/png', organization=self.org),
                             kill_switch.paused_reply())
            self.assertEqual(agent.chat_with_image(PHONE, b'x', 'image/png', organization=self.org),
                             kill_switch.paused_reply())
            self.assertEqual(STTService.transcribe(b'x', 'audio/ogg', organization=self.org).text, '')
        for m in (backend, whisper, gemini):
            m.assert_not_called()

    def test_voice_note_gets_the_notice_without_transcription(self):
        from apps.whatsapp.router import MessageRouter
        kill_switch.stop(reason='x', organization=self.org)
        with patch.object(MessageRouter, '_resolve_org', return_value=self.org), \
                patch.object(MessageRouter, '_send_and_log') as send, \
                patch.object(MessageRouter, '_transcribe_voice') as stt:
            MessageRouter.route({'id': 'wamid.k1', 'type': 'audio', 'audio': {'id': 'm1'}}, PHONE, 'pn')
        stt.assert_not_called()
        self.assertEqual(send.call_args.args[1], kill_switch.paused_reply())


class ApiTest(Base):
    URL = '/api/v1/ai/kill-switches/'

    def client_for(self, platform_role=None, role='admin'):
        u = User.objects.create(phone=f'+92300999{User.objects.count():04d}', role=role, platform_role=platform_role or '')
        api = APIClient()
        api.force_authenticate(u)
        return api, u

    def test_permissions(self):
        for role, platform_role in (('client', None), ('agent', None), ('admin', None), ('admin', 'billing_admin')):
            api, _ = self.client_for(platform_role, role)
            self.assertEqual(api.get(self.URL).status_code, 403, (role, platform_role))
            self.assertEqual(api.post(self.URL, {'scope': 'platform', 'reason': 'x'}).status_code, 403)
        self.assertIn(APIClient().get(self.URL).status_code, (401, 403))
        self.assertEqual(AIKillSwitch.objects.count(), 0)

    def test_stop_list_release_audited(self):
        api, admin = self.client_for('ai_admin')
        r = api.post(self.URL, {'scope': 'organization', 'organization': str(self.org.pk), 'reason': 'bad answers'},
                     format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['created_by'], admin.pk)
        self.assertEqual(api.post(self.URL, {'scope': 'organization', 'organization': str(self.org.pk),
                                             'reason': 'again'}, format='json').status_code, 409)
        self.assertEqual(len(api.get(self.URL).data['results']), 1)
        ops, _ = self.client_for('ops_admin')
        url = f"{self.URL}{r.data['id']}/release/"
        self.assertEqual(ops.post(url).status_code, 200)
        self.assertEqual(ops.post(url).status_code, 409)
        self.assertEqual(len(api.get(self.URL).data['results']), 0)
        self.assertEqual(len(api.get(self.URL, {'active': 'false'}).data['results']), 1)
        self.assertEqual(api.get(self.URL, {'active': 'x'}).status_code, 400)
        logs = AuditLog.objects.filter(target_model='AIKillSwitch', target_id=r.data['id'])
        self.assertEqual([x.after['released'] for x in logs.order_by('created_at')], [False, True])

    def test_validation(self):
        api, _ = self.client_for('super_admin')
        for body in ({'scope': 'market', 'reason': 'x'}, {'scope': 'platform'},
                     {'scope': 'platform', 'market': 'PK', 'reason': 'x'},
                     {'scope': 'organization', 'market': 'PK', 'reason': 'x'},
                     {'scope': 'market', 'market': 'ZZ', 'reason': 'x'}, {'scope': 'nope', 'reason': 'x'}):
            self.assertEqual(api.post(self.URL, body, format='json').status_code, 400, body)


class CommandTest(Base):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('ai_kill_switch', *args, stdout=out)
        return out.getvalue()

    def test_stop_list_release(self):
        self.assertIn('market PK', self.run_cmd('stop', '--market', 'pk', '--reason', 'incident'))
        s = AIKillSwitch.objects.get()
        self.assertIn(str(s.pk), self.run_cmd('list'))
        self.assertEqual(self.run_cmd('release', str(s.pk)), '')
        for bad in (('stop', '--platform'), ('stop', '--reason', 'x'), ('release', 'nope'), ('release', str(s.pk)),
                    ('stop', '--market', 'ZZ', '--reason', 'x'), ('stop', '--org', 'bad', '--reason', 'x')):
            with self.assertRaises(CommandError, msg=bad):
                self.run_cmd(*bad)
        self.assertLessEqual(s.created_at, timezone.now())
