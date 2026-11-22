"""
T-1022a — generic ``whatsapp.text`` outbox kind and its adoption at the alert sites
(hot lead, verification status, listing alerts; reminders are covered in
``test_follow_up_automation.py``).
"""
import json
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from apps.core import outbox
from apps.core.models import OutboxMessage
from apps.notifications.delivery import enqueue_whatsapp_text
from tests.factories import make_agent, make_developer, make_lead, make_property, make_user

PHONE = '+923001234577'


def _meta_ok(msg_id='wamid.TXT'):
    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'messages': [{'id': msg_id}]}
    return R()


def _rows(prefix):
    return list(OutboxMessage.objects.filter(kind='whatsapp.text', idempotency_key__startswith=prefix))


@patch('apps.whatsapp.client.is_within_24h_window', return_value=True)
@patch('apps.whatsapp.client.requests.post', return_value=_meta_ok())
class WhatsAppTextKindTest(TestCase):

    def _deliver(self, **kw):
        with patch('apps.core.tasks.dispatch_outbox_message.apply_async') as aa, \
                self.captureOnCommitCallbacks(execute=True):
            aa.side_effect = lambda args, **k: outbox.dispatch(args[0])
            msg = enqueue_whatsapp_text(PHONE, 'Hello agent', key='t:1', **kw)
        msg.refresh_from_db()
        return msg

    def test_delivered_through_real_client_and_payload_wiped(self, post, _win):
        msg = self._deliver()
        self.assertEqual((msg.status, msg.provider_ref), ('sent', 'wamid.TXT'))
        self.assertEqual(post.call_args.kwargs['json']['text']['body'], 'Hello agent')
        self.assertEqual(post.call_args.kwargs['json']['to'], PHONE.lstrip('+'))
        self.assertEqual(msg.payload, '{}')  # data minimisation (contains phones/text)

    def test_text_is_encrypted_at_rest(self, post, _win):
        from django.db import connection
        msg = enqueue_whatsapp_text(PHONE, 'Secret body', key='t:enc')
        with connection.cursor() as cur:
            cur.execute('SELECT payload FROM outbox_messages WHERE id = %s', [str(msg.pk)])
            raw = cur.fetchone()[0]
        self.assertNotIn('Secret body', raw)
        self.assertNotIn(PHONE, raw)

    def test_org_credentials_selected_at_delivery(self, post, _win):
        _, org = make_developer()
        from apps.whatsapp import client as wa
        with patch.object(wa, 'get_wa_client', wraps=wa.get_wa_client) as factory:
            msg = self._deliver(organization=org)
        self.assertEqual(msg.organization_id, org.pk)
        self.assertEqual(factory.call_args.args[0], org)

    def test_closed_window_is_permanent_failure(self, post, win):
        win.return_value = False
        msg = enqueue_whatsapp_text(PHONE, 'late', key='t:win')
        self.assertEqual(outbox.dispatch(msg.pk), 'failed')
        post.assert_not_called()

    def test_skip_window_check_is_passed_through(self, post, win):
        win.return_value = False
        msg = enqueue_whatsapp_text(PHONE, 'template flow', key='t:skip', skip_window_check=True)
        self.assertEqual(outbox.dispatch(msg.pk), 'sent')

    def test_transient_error_is_retried(self, post, _win):
        post.side_effect = ConnectionError('meta down')
        msg = enqueue_whatsapp_text(PHONE, 'x', key='t:retry')
        with patch('apps.core.outbox._schedule'), \
                patch('apps.resilience.resilience_engine.meta_cloud_api_circuit.call',
                      side_effect=ConnectionError('meta down')):
            self.assertEqual(outbox.dispatch(msg.pk), 'pending')

    def test_same_key_sends_once_and_no_recipient_is_noop(self, post, _win):
        a = enqueue_whatsapp_text(PHONE, 'x', key='t:dup')
        b = enqueue_whatsapp_text(PHONE, 'x', key='t:dup')
        self.assertEqual(a.pk, b.pk)
        self.assertIsNone(enqueue_whatsapp_text('', 'x', key='t:none'))
        self.assertIsNone(enqueue_whatsapp_text(PHONE, '', key='t:none2'))
        self.assertEqual(OutboxMessage.objects.count(), 1)

    def test_rolled_back_transaction_leaves_no_message(self, post, _win):
        try:
            with transaction.atomic():
                enqueue_whatsapp_text(PHONE, 'x', key='t:rb')
                raise RuntimeError('business change failed')
        except RuntimeError:
            pass
        self.assertFalse(OutboxMessage.objects.filter(idempotency_key='t:rb').exists())


class HotLeadAlertTest(TestCase):
    def test_threshold_crossing_queues_one_alert_for_the_agent(self):
        _, org = make_developer()
        agent_user, agent = make_agent(org=org)
        lead = make_lead(make_user(role='client'), org=org, assigned_agent=agent, score=40)
        lead.score = 85
        lead.save()
        (row,) = _rows(f'hot_lead:{lead.pk}:')
        self.assertEqual(json.loads(row.payload)['phone'], agent_user.phone.lstrip('+'))
        self.assertEqual(row.organization_id, org.pk)
        lead.score = 90  # still hot, no new crossing
        lead.save()
        self.assertEqual(len(_rows('hot_lead:')), 1)


class VerificationNoticeTest(TestCase):
    def test_notice_queued_once_per_status(self):
        from apps.verification.models import Verification
        owner = make_user(role='client')
        prop = make_property(owner=owner)
        v = Verification.objects.create(property=prop)
        v.status = 'passed'
        v.save()
        v.notes = 'unrelated edit'
        v.save()  # used to re-send the "approved" notice on every save
        (row,) = _rows(f'verification:{v.pk}:')
        self.assertEqual(row.idempotency_key, f'verification:{v.pk}:passed')
        self.assertEqual(json.loads(row.payload)['phone'], owner.phone.lstrip('+'))
        v.status = 'disputed'
        v.save()
        self.assertEqual(len(_rows(f'verification:{v.pk}:')), 2)


class ListingAlertTest(TestCase):
    def test_one_alert_per_listing_and_lead_even_on_rerun(self):
        from apps.properties.tasks import _notify_matching_leads
        _, org = make_developer()
        buyer = make_user(role='client')
        lead = make_lead(buyer, org=org, city_interest='Lahore', status='new', intent='buy')
        prop = make_property(org=org, city='Lahore')
        self.assertEqual(_notify_matching_leads(prop), 1)
        self.assertEqual(_notify_matching_leads(prop), 1)
        (row,) = _rows(f'listing_alert:{prop.pk}:{lead.pk}')
        self.assertEqual(json.loads(row.payload)['phone'], buyer.phone.lstrip('+'))
