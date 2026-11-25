"""T-2051 — thread photos, block, report, warnings (ADR-045)."""
import io
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone
from PIL import Image

from apps.core.models import AuditLog
from apps.enquiries import safety
from apps.enquiries import services as S
from apps.enquiries.models import Enquiry, EnquiryBlock, MessageAttachment, ThreadReport
from apps.leads.models import Appointment, ConversationMessage
from apps.moderation import services as M
from apps.notifications.models import Notification
from apps.viewings import services as V
from tests.factories import make_client, make_property
from tests.test_enquiries import THR, Base

MOD = '/api/v1/moderation/threads/'


def photo(name='a.jpg', color='red', fmt='JPEG', exif=True):
    out, img = io.BytesIO(), Image.new('RGB', (40, 30), color)
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = 'SecretCamera'
        kw['exif'] = e
    img.save(out, fmt, **kw)
    return SimpleUploadedFile(name, out.getvalue(), content_type='image/jpeg')


class ThreadBase(Base):
    def setUp(self):
        super().setUp()
        self.e = self.thread()

    def share(self):
        S.set_sharing(self.e, S.RESPONDER, True)

    def send(self, user, *files, body='', **extra):
        return self.api(user).post(f'{THR}{self.e.pk}/messages/', {'body': body, 'files': list(files)},
                                   format='multipart', **extra)

    def fetch(self, user, pk):
        return self.api(user).get(f'{THR}{self.e.pk}/attachments/{pk}/')

    def do(self, user, action, method='post', e=None, **body):
        return getattr(self.api(user), method)(f'{THR}{(e or self.e).pk}/{action}/', body, format='json')

    def moderator(self):
        u = make_client()
        u.role, u.platform_role = 'admin', 'ops_admin'
        u.save()
        return u


class AttachmentTest(ThreadBase):
    def test_photos_need_shared_contacts(self):
        r = self.send(self.buyer, photo())
        self.assertEqual((r.status_code, MessageAttachment.objects.count()), (409, 0))
        self.assertEqual(ConversationMessage.objects.count(), 1)          # nothing half-written

    def test_send_read_and_revoke(self):
        self.share()
        r = self.send(self.buyer, photo(), photo('b.png', 'blue', 'PNG', exif=False))
        self.assertEqual((r.status_code, r.data['body'], len(r.data['attachments'])), (201, '[photo]', 2))
        pk = r.data['attachments'][0]['id']
        got = self.fetch(self.owner, pk)
        self.assertEqual((got.status_code, got['Content-Type'], got['Cache-Control'], got['X-Content-Type-Options']),
                         (200, 'image/jpeg', 'private, no-store', 'nosniff'))
        self.assertEqual(Image.open(io.BytesIO(got.content)).size, (40, 30))
        self.assertNotIn(b'SecretCamera', got.content)
        for user in (self.other, make_client(), self.ub):
            self.assertEqual(self.fetch(user, pk).status_code, 404)
        self.assertEqual(self.api().get(f'{THR}{self.e.pk}/attachments/{pk}/').status_code, 401)
        S.set_sharing(self.e, S.USER, False)
        self.assertEqual(self.fetch(self.owner, pk).status_code, 404)
        self.assertEqual(self.fetch(self.buyer, pk).status_code, 200)
        self.assertEqual(self.read(self.owner, self.e).data[0]['attachments'], [])
        self.assertEqual(len(self.read(self.buyer, self.e).data[0]['attachments']), 2)

    def test_text_with_a_photo_is_masked_like_json(self):
        self.share()
        text = 'Mail me at a@mail.example'
        self.assertEqual(self.send(self.buyer, photo(), body=text).data['body'], self.say(self.buyer, self.e, text).data['body'])
        self.assertNotIn('a@mail', self.send(self.buyer, body=text).data['body'])

    def test_photo_of_another_thread_is_not_reachable(self):
        self.share()
        pk = self.send(self.buyer, photo()).data['attachments'][0]['id']
        self.e = self.thread(listing=self.live(make_property(org=self.org, title='B')))
        self.assertEqual(self.fetch(self.buyer, pk).status_code, 404)

    def test_encrypted_at_rest(self):
        self.share()
        self.send(self.buyer, photo())
        with connection.cursor() as cur:
            cur.execute('SELECT payload FROM message_attachments')
            raw = cur.fetchone()[0]
        self.assertNotEqual(raw, MessageAttachment.objects.get().payload)

    def test_refused_files(self):
        self.share()
        bad = SimpleUploadedFile('a.jpg', b'<svg onload=alert(1)>', content_type='image/jpeg')
        self.assertEqual(self.send(self.buyer, bad).status_code, 400)
        self.assertEqual(self.send(self.buyer, *[photo() for _ in range(4)]).status_code, 400)
        self.assertEqual(self.send(self.buyer).status_code, 400)
        with patch.object(safety, 'MAX_STORED', 10):
            self.assertEqual(self.send(self.buyer, photo()).status_code, 400)
        with patch.object(safety, 'MAX_INPUT', 10):
            self.assertEqual(self.send(self.buyer, photo()).status_code, 400)
        with patch.object(safety, 'MAX_FILES_PER_DAY', 1):
            self.assertEqual(self.send(self.buyer, photo()).status_code, 201)
            self.assertEqual(self.send(self.buyer, photo()).status_code, 429)
        S.close(self.e)
        self.assertEqual(self.send(self.buyer, photo()).status_code, 409)
        self.assertEqual(MessageAttachment.objects.count(), 1)

    def test_idempotency_compares_the_files(self):
        self.share()
        key = {'HTTP_IDEMPOTENCY_KEY': 'k1'}
        a = self.send(self.buyer, photo(), photo(), **key)
        b = self.send(self.buyer, photo(), photo(), **key)
        self.assertEqual((a.status_code, b.data['id'], b['Idempotent-Replayed']), (201, str(a.data['id']), 'true'))
        self.assertEqual(self.send(self.buyer, photo(color='green'), photo(), **key).status_code, 422)
        self.assertEqual(MessageAttachment.objects.count(), 2)


class IdTest(Base):
    def test_ids_that_look_like_card_numbers_survive_masking(self):
        pk = '54834722-4846-4330-b723-489454806123'
        p = self.live(make_property(org=self.org, title='Digits', id=pk))
        self.assertEqual(self.enquire(listing=p).status_code, 201)
        r = self.enquire(message='card 4111 1111 1111 1111 and 54834722-4846-4330-c723-489454806123')
        self.assertNotIn('4111', ConversationMessage.objects.get(enquiry_id=r.data['id']).body)


class WarningTest(ThreadBase):
    def test_codes(self):
        for text, codes in (
                ('Look at https://x.example', ['external_link']),
                ('Please send the token by JazzCash', ['payment_request']),
                ('Advance aaj bhej dein', ['payment_request']),
                ('بیعانہ آج بھیج دیں', ['payment_request']),
                ('Tell me the OTP you got', ['verification_code']),
                ('Is the deposit refundable?', []), ('Two bedrooms, 10 marla', []), ('', [])):
            self.assertEqual(safety.warnings(text), codes, text)

    def test_only_the_reader_is_warned(self):
        self.say(self.owner, self.e, 'Send the token on https://x.example')
        self.assertEqual(self.read(self.buyer, self.e).data[0]['warnings'], ['external_link', 'payment_request'])
        self.assertEqual(self.read(self.owner, self.e).data[0]['warnings'], [])


class BlockTest(ThreadBase):
    def test_consumer_blocks(self):
        p2 = self.live(make_property(org=self.org, title='Second'))
        e2 = self.thread(listing=p2)
        self.share()
        r = self.do(self.buyer, 'block')
        self.assertEqual((r.status_code, r.data['blocked'], r.data['status'], r.data['sharing']),
                         (200, True, 'closed', False))
        e2.refresh_from_db()
        self.assertEqual((e2.status, e2.contacts_shared), ('closed', False))
        self.assertIsNotNone(e2.consent_revoked_at)
        self.assertEqual(self.say(self.owner, e2).status_code, 409)
        r = self.enquire(listing=p2)
        self.assertEqual((r.status_code, r.data['detail'].code), (409, 'enquiry.blocked_by_you'))
        self.assertFalse(self.api(self.owner).get(f'{THR}{self.e.pk}/').data['blocked'])
        # the other side cannot lift it
        self.do(self.owner, 'block', 'delete')
        self.assertEqual(self.enquire(listing=p2).status_code, 409)
        self.assertFalse(self.do(self.buyer, 'block', 'delete').data['blocked'])
        self.assertEqual(self.enquire(listing=p2).status_code, 201)
        e2.refresh_from_db()
        self.assertEqual(e2.status, 'closed')

    def test_lister_blocks_without_saying_so(self):
        self.assertEqual(self.do(self.owner, 'block').data['blocked'], True)
        self.do(self.owner, 'block')                                         # twice is fine
        self.assertEqual(EnquiryBlock.objects.count(), 1)
        r = self.enquire()
        self.assertEqual((r.status_code, r.data['detail'].code), (409, 'enquiry.lister_unavailable'))
        self.assertFalse(self.api(self.buyer).get(f'{THR}{self.e.pk}/').data['blocked'])
        self.e.refresh_from_db()
        self.assertIsNone(self.e.consent_revoked_at)                        # the buyer's own choice stays
        mine = make_client()
        self.assertEqual(self.enquire(user=mine).status_code, 201)           # nobody else is affected

    def test_private_owner(self):
        seller = make_client()
        p = self.live(make_property(owner=seller, title='Private'), listing_owner_type='client')
        e = self.thread(listing=p)
        self.assertEqual(self.do(seller, 'block', e=e).status_code, 200)
        self.assertEqual(self.enquire(listing=p).status_code, 409)
        self.assertEqual(self.enquire(listing=self.p).status_code, 200)     # the organization's thread is open

    def test_platform_and_strangers(self):
        e = Enquiry.objects.get(pk=self.enquire(listing_id=None, requirement={'intent': 'buy', 'city': 'Lahore'}).data['id'])
        self.assertEqual(self.do(self.buyer, 'block', e=e).status_code, 409)
        for action in ('block', 'report'):
            self.assertEqual(self.do(self.other, action, category='spam').status_code, 404)

    def test_create_checks_again_under_the_lock(self):
        self.do(self.owner, 'block')
        with patch.object(S, 'check_allowed'), self.assertRaises(S.EnquiryError):
            S.create(self.buyer, message='hi', listing=self.p)

    def test_viewing_cannot_be_moved(self):
        a = Appointment.objects.create(lead=self.e.lead, enquiry=self.e, property=self.p, agent=self.agent,
                                       scheduled_at=timezone.now() + timedelta(days=2), duration_minutes=30)
        self.do(self.owner, 'block')
        with patch.object(V, '_hold') as hold, self.assertRaises(V.ViewingError):
            V.reschedule(a, self.buyer, timezone.now() + timedelta(days=3))
        hold.assert_not_called()


class ReportTest(ThreadBase):
    def report(self, user=None, category='fraud', text='asks for money'):
        return self.do(user or self.buyer, 'report', category=category, text=text)

    def test_report_queue_and_decision(self):
        mod = self.moderator()
        self.say(self.owner, self.e, 'Call 0300 1234567, send the token')
        self.assertEqual(self.report().status_code, 202)
        self.assertEqual(self.report(category='spam').status_code, 202)      # updates the open one
        r = ThreadReport.objects.get()
        self.assertEqual((r.category, r.priority, r.side), ('spam', 0, 'user'))
        self.report()
        self.assertEqual(self.api(mod).get('/api/v1/moderation/queues/').data['threads'], 1)
        rows = self.api(mod).get(MOD).data
        rows = rows['results'] if isinstance(rows, dict) else rows
        self.assertEqual((rows[0]['priority'], 'messages' in rows[0], 'reporter' in rows[0]), (2, False, False))
        detail = self.api(mod).get(f'{MOD}{r.pk}/').data
        self.assertIn('0300 1234567', detail['messages'][0]['body'])          # the evidence, unmasked
        self.assertEqual(detail['messages'][0]['warnings'], ['payment_request'])
        self.assertEqual(AuditLog.objects.filter(actor=mod, target_id=str(r.pk)).count(), 1)
        d = self.api(mod).post(f'{MOD}{r.pk}/decision/', {'decision': 'close'}, format='json')
        self.assertEqual((d.status_code, d.data['status']), (200, 'closed'))
        self.e.refresh_from_db()
        self.assertEqual(self.e.status, 'closed')
        self.assertTrue(Notification.objects.filter(user=self.buyer, title='Your report was reviewed').exists())
        self.assertEqual(self.api(mod).get(f'{MOD}{r.pk}/').data['messages'], [])   # decided: not readable
        self.assertEqual(self.api(mod).post(f'{MOD}{r.pk}/decision/', {'decision': 'dismiss'}, format='json').status_code,
                         409)

    def test_photos_for_moderators_only_while_open(self):
        mod = self.moderator()
        self.share()
        pk = self.send(self.owner, photo()).data['attachments'][0]['id']
        self.report()
        r = ThreadReport.objects.get()
        url = f'{MOD}{r.pk}/attachments/{pk}/'
        S.set_sharing(self.e, S.USER, False)
        self.assertEqual(self.api(mod).get(url).status_code, 200)
        self.assertEqual(AuditLog.objects.filter(actor=mod, target_id=str(r.pk)).count(), 1)
        self.api(mod).post(f'{MOD}{r.pk}/decision/', {'decision': 'dismiss'}, format='json')
        self.assertEqual(self.api(mod).get(url).status_code, 404)

    def test_access(self):
        self.report()
        r = ThreadReport.objects.get()
        support = self.moderator()
        support.platform_role = 'support_agent'
        support.save()
        for user in (self.buyer, self.owner, support):
            for path in ('', f'{r.pk}/'):
                self.assertEqual(self.api(user).get(MOD + path).status_code, 403)
        self.assertEqual(self.api(self.moderator()).get(MOD, {'status': 'x'}).status_code, 400)

    def test_a_moderator_does_not_judge_their_own_thread(self):
        mod = self.moderator()
        e = self.thread(user=mod, listing=self.p)
        self.do(self.owner, 'report', e=e, category='abuse')
        r = ThreadReport.objects.get()
        self.assertEqual(self.api(mod).get(f'{MOD}{r.pk}/').status_code, 403)
        self.assertEqual(self.api(mod).post(f'{MOD}{r.pk}/decision/', {'decision': 'dismiss'}, format='json').status_code,
                         403)

    def test_limit_and_overdue(self):
        mod = self.moderator()
        with patch.object(safety, 'MAX_REPORTS_PER_DAY', 1):
            self.assertEqual(self.report().status_code, 202)
            e2 = self.thread(listing=self.live(make_property(org=self.org, title='B')))
            self.assertEqual(self.do(self.buyer, 'report', e=e2, category='spam').status_code, 429)
        ThreadReport.objects.update(due_at=timezone.now() - timedelta(hours=1))
        self.assertEqual((M.notify_overdue(), M.notify_overdue()), (1, 0))
        self.assertTrue(Notification.objects.filter(user=mod, title='Moderation overdue').exists())


class ErasureTest(ThreadBase):
    def test_consumer_and_responder(self):
        self.share()
        self.send(self.buyer, photo())
        self.send(self.owner, photo())
        self.do(self.owner, 'report', category='spam', text='private words')
        S.erase_user(self.owner)
        self.assertEqual(MessageAttachment.objects.get().sender, self.buyer)
        r = ThreadReport.objects.get()
        self.assertEqual((r.text, r.reporter), ('[REDACTED]', None))
        self.do(self.buyer, 'block')
        self.assertEqual(S.erase_user(self.buyer), 1)
        for model in (MessageAttachment, ThreadReport, EnquiryBlock, Enquiry):
            self.assertEqual(model.objects.count(), 0)
