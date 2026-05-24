"""
Tests for feature_talk_to_agent — agent handover protocol.
Run with: python manage.py test tests.test_agent_handover --settings=config.settings.test
"""
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, TransactionTestCase
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from channels.testing import WebsocketCommunicator
from rest_framework_simplejwt.tokens import AccessToken
from tests.factories import make_org, make_developer, make_agent, make_user
from apps.whatsapp.models import WhatsAppSession


def _make_jwt(user):
    """Return a valid JWT access token string for the given user."""
    return str(AccessToken.for_user(user))


class TestJWTMiddleware(TransactionTestCase):
    """
    Middleware tests — we connect to the consumer URL and check the close code.
    Uses TransactionTestCase to avoid DB connection issues with async channels code.
    """

    def setUp(self):
        self.dev_user, self.org = make_developer()

    async def _connect(self, token_param):
        from config.asgi import application
        url = f"/ws/agent/org/{self.org.id}/?token={token_param}"
        communicator = WebsocketCommunicator(application, url)
        connected, close_code = await communicator.connect()
        await communicator.disconnect()
        return connected, close_code

    def test_missing_token_rejected_4001(self):
        from asgiref.sync import async_to_sync
        connected, code = async_to_sync(self._connect)('')
        self.assertFalse(connected)
        self.assertEqual(code, 4001)

    def test_invalid_token_rejected_4001(self):
        from asgiref.sync import async_to_sync
        connected, code = async_to_sync(self._connect)('not.a.real.jwt')
        self.assertFalse(connected)
        self.assertEqual(code, 4001)


class TestWebSocketConsumer(TransactionTestCase):

    def setUp(self):
        self.dev_user, self.org = make_developer()
        self.agent_user, _ = make_agent(org=self.org)
        client_user = make_user()
        self.session = WhatsAppSession.objects.create(
            phone='+15550003333',
            user=client_user,
            organization=self.org,
        )

    def _communicator(self, user):
        from config.asgi import application
        token = _make_jwt(user)
        url = f"/ws/agent/org/{self.org.id}/?token={token}"
        return WebsocketCommunicator(application, url)

    def test_authenticated_agent_connects(self):
        from asgiref.sync import async_to_sync

        async def _run():
            comm = self._communicator(self.agent_user)
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            msg = await comm.receive_json_from(timeout=2)
            self.assertEqual(msg["type"], "connected")
            await comm.disconnect()

        async_to_sync(_run)()

    def test_cross_org_agent_rejected_4003(self):
        from asgiref.sync import async_to_sync

        other_dev, other_org = make_developer()

        async def _run():
            from config.asgi import application
            token = _make_jwt(other_dev)
            url = f"/ws/agent/org/{self.org.id}/?token={token}"
            comm = WebsocketCommunicator(application, url)
            connected, code = await comm.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4003)

        async_to_sync(_run)()

    def test_client_role_rejected_4003(self):
        from asgiref.sync import async_to_sync

        client_user = make_user()  # role='client' by default

        async def _run():
            from config.asgi import application
            token = _make_jwt(client_user)
            url = f"/ws/agent/org/{self.org.id}/?token={token}"
            comm = WebsocketCommunicator(application, url)
            connected, code = await comm.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4003)

        async_to_sync(_run)()


class TestHandoverRouterBypass(TestCase):

    def setUp(self):
        from apps.whatsapp.models import OrgWhatsAppConfig
        self.dev_user, self.org = make_developer()
        self.client_user = make_user()
        self.session = WhatsAppSession.objects.create(
            phone='15550004444',
            user=self.client_user,
            organization=self.org,
            conversation_mode='AGENT_MANAGED',
        )
        OrgWhatsAppConfig.objects.create(
            organization=self.org,
            phone_number_id='TEST_PHONE_ID',
            is_active=True,
        )

    def test_agent_managed_bypasses_llm_and_broadcasts(self):
        from apps.whatsapp.router import MessageRouter

        message_data = {'type': 'text', 'text': {'body': 'I want a 3-bed house'}}

        with patch.object(MessageRouter, '_broadcast_to_agent_room') as mock_broadcast, \
             patch('apps.ai.service.get_service_manager') as mock_svc:

            MessageRouter.route(message_data, '15550004444', 'TEST_PHONE_ID')

        mock_broadcast.assert_called_once()
        mock_svc.assert_not_called()

    def test_ai_managed_calls_llm(self):
        from apps.whatsapp.router import MessageRouter

        self.session.conversation_mode = 'AI_MANAGED'
        self.session.save(update_fields=['conversation_mode'])

        message_data = {'type': 'text', 'text': {'body': 'I want a 3-bed house'}}
        mock_reply = MagicMock()
        mock_reply.process.return_value = 'AI reply'

        with patch('apps.ai.service.get_service_manager', return_value=mock_reply):
            MessageRouter.route(message_data, '15550004444', 'TEST_PHONE_ID')

        mock_reply.process.assert_called_once()


class TestHandoverEndpoints(TestCase):

    def setUp(self):
        self.dev_user, self.org = make_developer()
        self.agent_user, _ = make_agent(org=self.org)
        self.client_user = make_user()
        self.session = WhatsAppSession.objects.create(
            phone='15550005555',
            user=self.client_user,
            organization=self.org,
        )
        self.api = APIClient()

    def tearDown(self):
        cache.delete(f"wa:agent_lock:{self.session.id}")

    def _auth(self, user):
        self.api.force_authenticate(user=user)

    def test_take_control_sets_agent_managed(self):
        self._auth(self.agent_user)
        url = f'/api/v1/whatsapp/sessions/{self.session.id}/take-control/'
        response = self.api.post(url)
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.conversation_mode, 'AGENT_MANAGED')
        lock = cache.get(f"wa:agent_lock:{self.session.id}")
        self.assertIsNotNone(lock)

    def test_409_when_session_already_held(self):
        self._auth(self.agent_user)
        url = f'/api/v1/whatsapp/sessions/{self.session.id}/take-control/'
        r1 = self.api.post(url)
        self.assertEqual(r1.status_code, 200)

        agent_b, _ = make_agent(org=self.org)
        self.api.force_authenticate(user=agent_b)
        r2 = self.api.post(url)
        self.assertEqual(r2.status_code, 409)
        self.assertIn('held_by', r2.data)

    def test_client_role_forbidden(self):
        self.api.force_authenticate(user=self.client_user)
        url = f'/api/v1/whatsapp/sessions/{self.session.id}/take-control/'
        response = self.api.post(url)
        self.assertEqual(response.status_code, 403)

    def test_release_control_reverts_to_ai(self):
        cache.set(f"wa:agent_lock:{self.session.id}", str(self.agent_user.id), timeout=180)
        self.session.conversation_mode = 'AGENT_MANAGED'
        self.session.save(update_fields=['conversation_mode'])

        self._auth(self.agent_user)
        url = f'/api/v1/whatsapp/sessions/{self.session.id}/release-control/'
        response = self.api.post(url)
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.conversation_mode, 'AI_MANAGED')
        self.assertIsNone(cache.get(f"wa:agent_lock:{self.session.id}"))

    def test_non_holder_cannot_release(self):
        agent_b, _ = make_agent(org=self.org)
        cache.set(f"wa:agent_lock:{self.session.id}", str(self.agent_user.id), timeout=180)
        self.session.conversation_mode = 'AGENT_MANAGED'
        self.session.save(update_fields=['conversation_mode'])

        self.api.force_authenticate(user=agent_b)
        url = f'/api/v1/whatsapp/sessions/{self.session.id}/release-control/'
        response = self.api.post(url)
        self.assertEqual(response.status_code, 403)


class TestCeleryRevertTask(TestCase):

    def setUp(self):
        self.dev_user, self.org = make_developer()
        client_user = make_user()
        self.session = WhatsAppSession.objects.create(
            phone='15550006666',
            user=client_user,
            organization=self.org,
            conversation_mode='AGENT_MANAGED',
        )

    def tearDown(self):
        cache.delete(f"wa:agent_lock:{self.session.id}")

    def test_reverts_session_when_lock_expired(self):
        cache.delete(f"wa:agent_lock:{self.session.id}")

        from apps.whatsapp.tasks import revert_orphaned_agent_sessions
        count = revert_orphaned_agent_sessions()

        self.assertEqual(count, 1)
        self.session.refresh_from_db()
        self.assertEqual(self.session.conversation_mode, 'AI_MANAGED')

    def test_does_not_revert_when_lock_active(self):
        cache.set(f"wa:agent_lock:{self.session.id}", 'some-user-id', timeout=180)

        from apps.whatsapp.tasks import revert_orphaned_agent_sessions
        count = revert_orphaned_agent_sessions()

        self.assertEqual(count, 0)
        self.session.refresh_from_db()
        self.assertEqual(self.session.conversation_mode, 'AGENT_MANAGED')


class TestOrgIsolation(TransactionTestCase):
    """
    An agent from Org A must never receive messages belonging to Org B.
    """

    def setUp(self):
        self.dev_a, self.org_a = make_developer()
        self.dev_b, self.org_b = make_developer()
        self.agent_a, _ = make_agent(org=self.org_a)
        self.client_b = make_user()
        self.session_b = WhatsAppSession.objects.create(
            phone='15550007777',
            user=self.client_b,
            organization=self.org_b,
            conversation_mode='AGENT_MANAGED',
        )

    def test_org_a_agent_cannot_receive_org_b_messages(self):
        from asgiref.sync import async_to_sync
        from config.asgi import application

        async def _run():
            token_a = _make_jwt(self.agent_a)
            comm_a = WebsocketCommunicator(
                application, f"/ws/agent/org/{self.org_a.id}/?token={token_a}"
            )
            connected, _ = await comm_a.connect()
            self.assertTrue(connected)
            _ = await comm_a.receive_json_from(timeout=2)

            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                f"org_{self.org_b.id}_agent_room",
                {
                    "type":       "agent_message",
                    "event":      "inbound_message",
                    "session_id": str(self.session_b.id),
                    "phone":      "15550007777",
                    "message":    {"type": "text", "text": {"body": "secret org B message"}},
                    "timestamp":  "2026-05-24T00:00:00Z",
                    "lead_id":    None,
                },
            )

            # Agent A should receive nothing — timeout means isolation holds
            received_something = False
            try:
                await comm_a.receive_json_from(timeout=1)
                received_something = True
            except Exception:
                pass  # expected: no message arrives

            self.assertFalse(received_something)
            try:
                await comm_a.disconnect()
            except BaseException:
                pass

        async_to_sync(_run)()


class TestConversationModeField(TestCase):

    def test_default_is_ai_managed(self):
        user = make_user()
        session = WhatsAppSession.objects.create(phone='+15550001111', user=user)
        self.assertEqual(session.conversation_mode, 'AI_MANAGED')

    def test_can_set_agent_managed(self):
        user = make_user()
        session = WhatsAppSession.objects.create(
            phone='+15550002222',
            user=user,
            conversation_mode='AGENT_MANAGED',
        )
        session.refresh_from_db()
        self.assertEqual(session.conversation_mode, 'AGENT_MANAGED')
