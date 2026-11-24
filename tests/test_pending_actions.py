"""
T-2012 — durable PendingAction confirmations (ADR-030, MVP acceptance 8).

The runtime never executes a ``requires_confirmation`` tool; only the user's
reply (or the API) does, once, with the stored arguments.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.ai import confirmation, llm, runtime
from apps.ai import tool_registry as R
from apps.ai._tools_context import set_context
from apps.ai.models import PendingAction
from apps.ai.schemas import IntentResult
from apps.ai.service import AIServiceManager
from apps.config.services import SystemConfigService
from apps.leads.models import Lead
from apps.properties.models import Property
from tests.factories import make_client, make_org, make_user
from tests.test_runtime import Scripted, calls, final

PHONE = '923001239900'
ARGS = {'city': 'Lahore', 'location': 'DHA 6', 'area_marla': 10.0, 'price': 25_000_000,
        'property_type': 'residential', 'description': 'Corner plot, owner abroad'}
S = PendingAction.Status


class Base(TestCase):
    def setUp(self):
        self.user = make_client(phone='+' + PHONE)

    def propose(self, tool='list_property', args=None, org=None, phone=PHONE, user='self'):
        set_context(self.user if user == 'self' else user, phone, org=org)
        result = confirmation.propose(R.REGISTRY[tool], ARGS if args is None else args)
        return PendingAction.objects.get(pk=result['pending_action_id'])

    def listings(self):
        return Property.objects.filter(owner=self.user).count()


class RuntimeGateTest(Base):
    def test_tool_is_not_run_and_turn_ends_with_the_prompt(self):
        set_context(self.user, PHONE)
        b = Scripted('gemini', [calls(('list_property', ARGS), ('connect_to_agent', {'city': 'Lahore'})),
                                final('must not be reached')])
        with patch('apps.ai.tools.list_property', autospec=True) as fn:
            turn = runtime.run_turn(b, 'yes', [], [R.REGISTRY['list_property'], R.REGISTRY['connect_to_agent']], 's')
        fn.assert_not_called()
        action = PendingAction.objects.get()
        self.assertEqual((turn.text, turn.steps), (action.prompt, 1))
        self.assertEqual([c['status'] for c in turn.tool_calls], ['proposed', 'skipped'])
        self.assertEqual(turn.tool_calls[0]['pending_action'], str(action.pk))

    def test_every_confirmable_registry_tool_is_gated_and_describable(self):
        from apps.ai import proposals
        names = {n for n, s in R.REGISTRY.items() if s.requires_confirmation}
        self.assertEqual(names, set(proposals._BUILDERS))
        for s in R.REGISTRY.values():       # a committing tool must ask first
            self.assertEqual(s.commits, s.requires_confirmation, s.name)

    def test_unknown_confirmable_tool_fails_closed(self):
        ran = []

        def wipe() -> dict:
            """Wipe."""
            ran.append(1)
            return {}
        set_context(self.user, PHONE)
        spec = R.ToolSpec('wipe', side_effect=R.LOW, requires_confirmation=True, func=wipe)
        turn = runtime.run_turn(Scripted('g', [calls(('wipe', {})), final('no')]), 'x', [], [spec], 's')
        self.assertEqual((ran, turn.tool_calls[0]['status']), ([], 'error'))

    def test_non_interactive_turn_cannot_propose(self):
        set_context(self.user, PHONE, interactive=False)
        self.assertIn('error', confirmation.propose(R.REGISTRY['list_property'], ARGS))
        self.assertFalse(PendingAction.objects.exists())

    def test_agent_request_goes_through_the_tool(self):
        """The _is_agent_request keyword bypass is gone: no lead without a YES."""
        from apps.ai.agent import RealTronAgent
        result = llm.LLMResult(text='x', provider='p', model='m', prompt=None)
        with patch('apps.ai.llm.any_configured', return_value=True), \
                patch('apps.ai.llm.chat', return_value=result) as chat:
            RealTronAgent().chat(PHONE, 'connect me to an agent in Lahore', user=self.user)
        chat.assert_called_once()
        self.assertFalse(Lead.objects.filter(user=self.user, intent='buy').exists())


class ConfirmTest(Base):
    def test_record_of_an_executed_action(self):
        a = self.propose()
        self.assertIn('listed successfully', confirmation.resolve_reply(PHONE, 'Yes', self.user))
        a.refresh_from_db()
        self.assertEqual((a.status, a.confirmed_via, a.awaiting_reply), (S.EXECUTED, 'message', False))
        self.assertTrue(a.confirmed_at and a.resolved_at and a.result['listing_id'])
        self.assertEqual(self.listings(), 1)

    def test_args_and_prompt_encrypted_at_rest(self):
        self.propose()
        with connection.cursor() as c:
            c.execute('SELECT args, prompt FROM ai_pending_actions')
            for value in c.fetchone():
                self.assertTrue(value.startswith('enc1:'))

    def test_only_the_next_message_confirms(self):
        a = self.propose()
        self.assertIsNone(confirmation.resolve_reply(PHONE, 'what is the commission?', self.user))
        self.assertIsNone(confirmation.resolve_reply(PHONE, 'yes', self.user))
        a.refresh_from_db()
        self.assertEqual((a.status, self.listings()), (S.PENDING, 0))

    def test_scoped_to_conversation_and_user(self):
        org = make_org(name='A')
        self.propose(org=org)
        self.assertIsNone(confirmation.resolve_reply(PHONE, 'yes', self.user, make_org(name='B')))
        self.assertIsNone(confirmation.resolve_reply('923000000000', 'yes', self.user, org))
        self.assertIsNone(confirmation.resolve_reply(PHONE, 'yes', make_client(), org))
        self.assertEqual(self.listings(), 0)

    def test_expired(self):
        a = self.propose()
        PendingAction.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIn('expired', confirmation.resolve_reply(PHONE, 'yes', self.user))
        a.refresh_from_db()
        self.assertEqual((a.status, self.listings()), (S.EXPIRED, 0))

    def test_claimed_action_cannot_run_again(self):
        a = self.propose()
        stale = PendingAction.objects.get(pk=a.pk)
        confirmation.confirm(a, 'api')
        with self.assertRaises(confirmation.NotConfirmable):
            confirmation.confirm(stale, 'message')
        self.assertEqual(self.listings(), 1)

    def test_tampered_args_refused(self):
        a = self.propose()
        a.args = a.args.replace('25000000', '1')
        a.save(update_fields=['args'])
        self.assertIn('no longer available', confirmation.resolve_reply(PHONE, 'yes', self.user))
        a.refresh_from_db()
        self.assertEqual((a.status, self.listings()), (S.FAILED, 0))

    def test_feature_turned_off_before_confirmation(self):
        self.propose()
        SystemConfigService.set('feature_property_listing', 'false')
        self.assertIn('no longer available', confirmation.resolve_reply(PHONE, 'yes', self.user))
        self.assertEqual(self.listings(), 0)

    def test_tool_exception_fails_without_leaking(self):
        a = self.propose()
        with patch('apps.ai.tools.list_property', side_effect=RuntimeError('db secret')):
            text = confirmation.resolve_reply(PHONE, 'yes', self.user)
        a.refresh_from_db()
        self.assertNotIn('secret', text)
        self.assertEqual(a.status, S.FAILED)

    def test_grant_is_cleared_after_execution(self):
        self.propose()
        confirmation.resolve_reply(PHONE, 'yes', self.user)
        from apps.ai.tools import list_property
        set_context(self.user, PHONE)
        self.assertTrue(list_property(**ARGS)['confirmation_required'])

    def test_one_open_action_per_conversation(self):
        first, second = self.propose(), self.propose('connect_to_agent', {'city': 'Lahore'})
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), (S.SUPERSEDED, S.PENDING))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PendingAction.objects.create(conversation_key=second.conversation_key, tool='x', side_effect='low',
                                         args='{}', args_hash='', prompt='p', expires_at=timezone.now())

    def test_purge(self):
        stuck, old = self.propose(), self.propose(phone='923001239901')
        now = timezone.now()
        PendingAction.objects.filter(pk=stuck.pk).update(status=S.EXECUTING, confirmed_at=now - timedelta(hours=2))
        PendingAction.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=91))
        due = self.propose(phone='923001239902')
        PendingAction.objects.filter(pk=due.pk).update(expires_at=now)
        self.assertEqual(confirmation.purge(), 1)
        self.assertEqual(dict(PendingAction.objects.values_list('pk', 'status')),
                         {stuck.pk: S.FAILED, due.pk: S.EXPIRED})


class ServiceTest(Base):
    def run_service(self, message, chat, **kw):
        svc = AIServiceManager()
        svc._agent = MagicMock()
        svc._agent._load_history.return_value = []
        svc._agent.chat.side_effect = chat
        with patch('apps.ai.service.IntentClassifier.classify',
                   return_value=IntentResult(intent='general_query', confidence=0.5)), \
                patch('apps.ai.context.DynamicContextBuilder.build', return_value=''):
            return svc.process(PHONE, message, self.user, **kw), svc._agent

    def chat_proposing(self, reply=None):
        def chat(phone, *a, **kw):
            action = self.propose()
            return reply or action.prompt
        return chat

    def test_yes_is_handled_without_the_model(self):
        self.run_service('list my house', self.chat_proposing())
        reply, agent = self.run_service('yes', AssertionError('model called'))
        self.assertIn('listed successfully', reply)
        agent.chat.assert_not_called()

    def test_summary_the_user_never_saw_is_withdrawn(self):
        self.run_service('list my house', self.chat_proposing(reply='All done!'))
        self.assertEqual(PendingAction.objects.get().status, S.SUPERSEDED)

    def test_proposal_is_not_grounding_checked(self):
        with patch('apps.ai.compliance_filters.ComplianceEngine.check_output') as check:
            reply, _ = self.run_service('list my house', self.chat_proposing())
        check.assert_not_called()
        self.assertIn('Reply *YES*', reply)

    def test_non_interactive_call_neither_confirms_nor_closes(self):
        a = self.propose()
        _, agent = self.run_service('yes', lambda *a, **k: 'draft', interactive=False)
        self.assertIs(agent.chat.call_args.kwargs['interactive'], False)
        a.refresh_from_db()
        self.assertEqual((a.status, a.awaiting_reply, self.listings()), (S.PENDING, True, 0))

    def test_literal_audit_request_also_asks(self):
        from apps.ai.schemas import AuditInput
        set_context(self.user, PHONE)
        reply = AIServiceManager._direct_property_audit(
            AuditInput(city='Lahore', location='DHA', estimated_value_pkr=10_000_000), 'en')
        self.assertIn('Reply *YES*', reply)


class RouterTest(Base):
    def route(self, message):
        from apps.whatsapp.router import MessageRouter
        with patch('apps.whatsapp.router.get_wa_client') as client:
            client.return_value.send_text.return_value = {'messages': [{'id': 'out1'}]}
            MessageRouter.route({'id': 'in1', **message}, PHONE)

    def test_whatsapp_yes_executes(self):
        self.propose()
        self.route({'type': 'text', 'text': {'body': 'Yes'}})
        self.assertEqual(self.listings(), 1)

    def test_reset_withdraws(self):
        a = self.propose()
        self.route({'type': 'text', 'text': {'body': 'reset'}})
        a.refresh_from_db()
        self.assertEqual(a.status, S.SUPERSEDED)

    def test_image_and_agent_takeover_close_the_window(self):
        from apps.whatsapp.models import WhatsAppSession
        from apps.whatsapp.router import MessageRouter
        a = self.propose()
        with patch.object(MessageRouter, '_download_media', return_value=(b'x', 'image/jpeg')), \
                patch.object(MessageRouter, '_handle_image', return_value='nice'):
            self.route({'type': 'image', 'image': {'id': 'm'}})
        a.refresh_from_db()
        self.assertFalse(a.awaiting_reply)
        b = self.propose()
        WhatsAppSession.objects.filter(phone=PHONE).update(
            conversation_mode=WhatsAppSession.ConversationMode.AGENT_MANAGED)
        with patch.object(MessageRouter, '_broadcast_to_agent_room'):
            self.route({'type': 'text', 'text': {'body': 'yes'}})
        b.refresh_from_db()
        self.assertEqual((b.status, b.awaiting_reply, self.listings()), (S.PENDING, False, 0))


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.action = self.propose()

    def url(self, name):
        return reverse(f'pending-action-{name}', args=[self.action.pk])

    def test_list_shows_own_open_actions_without_args(self):
        self.propose(user=make_client(), phone='923001239903')
        data = self.api.get(reverse('pending-action-list')).json()
        rows = data['results'] if isinstance(data, dict) else data
        self.assertEqual([r['id'] for r in rows], [str(self.action.pk)])
        self.assertNotIn('args', rows[0])

    def test_confirm_then_409(self):
        r = self.api.post(self.url('confirm'))
        self.assertEqual((r.status_code, r.json()['action']['status']), (200, 'executed'))
        r = self.api.post(self.url('confirm'))
        self.assertEqual((r.status_code, r.json()['error_info']['code']), (409, 'action_not_pending'))
        self.assertEqual(self.listings(), 1)

    def test_button_works_after_the_reply_window_closed(self):
        confirmation.close_reply_window(PHONE)
        self.assertEqual(self.api.post(self.url('confirm')).status_code, 200)

    def test_decline(self):
        self.assertEqual(self.api.post(self.url('decline')).json()['status'], 'declined')
        self.assertEqual(self.api.post(self.url('confirm')).status_code, 409)
        self.assertEqual(self.listings(), 0)

    def test_expired_is_409(self):
        PendingAction.objects.update(expires_at=timezone.now())
        self.assertEqual(self.api.post(self.url('confirm')).status_code, 409)

    def test_others_cannot_see_or_confirm(self):
        for role in ('client', 'admin', 'developer'):
            api = APIClient()
            api.force_authenticate(make_user(role=role))
            for name in ('confirm', 'decline'):
                self.assertEqual(api.post(self.url(name)).status_code, 404)
        self.assertIn(APIClient().post(self.url('confirm')).status_code, (401, 403))

    def test_blocked_user_cannot_confirm(self):
        from apps.whatsapp.models import WhatsAppSession
        WhatsAppSession.objects.create(phone=PHONE, user=self.user,
                                       conversation_mode=WhatsAppSession.ConversationMode.BLOCKED)
        self.assertEqual(self.api.post(self.url('confirm')).status_code, 403)
        self.assertEqual(self.listings(), 0)
