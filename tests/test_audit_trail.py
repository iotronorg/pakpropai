"""T-3070 — the unified audit trail: one append-only, hash-chained log (GAP-104, ADR-060)."""
import io
import threading
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.compliance.models import PrivacyAuditLog
from apps.core import audit as A
from apps.core.models import AuditEvent, AuditLog
from apps.organizations.models import OrganizationMembership
from apps.security.models import ApiSecurityEvent
from tests.factories import make_client, make_developer, make_membership, make_org, make_user
from tests.test_runtime import LOCMEM

URL = '/api/v1/audit-events/'


def _raw(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)


@override_settings(CACHES=LOCMEM)
class ChainTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_rows_are_chained_and_verify(self):
        a = A.record('config.changed', meta={'key': 'x'}, personal={'before': 1, 'after': 2})
        b = A.record('config.changed', category='ai')
        self.assertEqual((a.seq, b.seq, a.prev_hash, b.prev_hash), (1, 2, A.GENESIS, a.record_hash))
        v = A.verify()
        self.assertEqual((v.ok, v.checked, v.head_seq, v.head_hash), (True, 2, 2, b.record_hash))
        with self.assertRaises(ValueError):
            A.record('x.y', category='gossip')

    def test_database_refuses_changes_and_deletes(self):
        e = A.record('membership.role_changed', personal={'detail': 'x'})
        for sql in ("UPDATE audit_events SET action = 'nothing.happened' WHERE id = %s",
                    "UPDATE audit_events SET personal = '{\"detail\": \"y\"}' WHERE id = %s",
                    "UPDATE audit_events SET personal = '{}' WHERE id = %s",          # redaction needs redacted_at
                    "DELETE FROM audit_events WHERE id = %s"):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                _raw(sql, [e.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=e.pk).delete()

    def test_tampering_is_found(self):
        """Someone who can turn the trigger off (a DB owner) still cannot hide a change."""
        rows = [A.record('admin.update', personal={'detail': f'row {i}'}) for i in range(4)]
        _raw('ALTER TABLE audit_events DISABLE TRIGGER audit_events_guard')
        _raw("UPDATE audit_events SET target_id = 'other' WHERE seq = 2")
        v = A.verify()
        self.assertEqual((v.ok, v.broken_at, v.reason, v.checked), (False, 2, 'row changed', 1))
        _raw("UPDATE audit_events SET target_id = '' WHERE seq = 2")
        _raw("UPDATE audit_events SET personal = '{\"detail\": \"forged\"}' WHERE seq = 3")
        self.assertEqual((A.verify().broken_at, A.verify().reason), (3, 'personal data changed'))
        _raw("UPDATE audit_events SET personal = %s WHERE seq = 3", ['{"detail": "row 2"}'])
        _raw('DELETE FROM audit_events WHERE seq = 2')
        self.assertEqual((A.verify().broken_at, A.verify().reason), (3, 'missing rows before 3'))
        _raw('ALTER TABLE audit_events ENABLE TRIGGER audit_events_guard')
        self.assertEqual(len(rows), 4)

    def test_redaction_keeps_the_chain(self):
        user = make_client(email='amna@mail.example')
        A.record('admin.update', actor=user, personal={'ip': '203.0.113.5'})
        A.record('admin.update', target=user, personal={'detail': 'x'})
        A.record('admin.update', personal={'after': {'phone': user.phone}})
        keep = A.record('admin.update', personal={'detail': 'someone else'})
        self.assertEqual(A.redact_person(user, [user.phone, user.phone.lstrip('+')]), 3)
        self.assertEqual(AuditEvent.objects.filter(personal={}, redacted_at__isnull=False).count(), 3)
        keep.refresh_from_db()
        self.assertEqual(keep.personal, {'detail': 'someone else'})
        self.assertTrue(A.verify().ok)

    def test_command_and_daily_task(self):
        A.record('admin.update')
        out = io.StringIO()
        call_command('verify_audit_chain', stdout=out)
        self.assertIn('OK: 1 rows', out.getvalue())
        from apps.core.tasks import verify_audit_chain
        self.assertTrue(verify_audit_chain()['ok'])
        _raw('ALTER TABLE audit_events DISABLE TRIGGER audit_events_guard')
        _raw("UPDATE audit_events SET action = 'nothing.happened'")
        _raw('ALTER TABLE audit_events ENABLE TRIGGER audit_events_guard')
        with self.assertRaises(CommandError):
            call_command('verify_audit_chain', stdout=io.StringIO())
        with self.assertLogs('apps.core.audit', 'ERROR'):
            self.assertFalse(verify_audit_chain()['ok'])

    @override_settings(AUDIT_CHAIN_KEY='another key')
    def test_the_key_signs_the_chain(self):
        A.record('admin.update')
        with override_settings(AUDIT_CHAIN_KEY='changed'):
            self.assertEqual(A.verify().reason, 'row changed')
        self.assertTrue(A.verify().ok)


@override_settings(CACHES=LOCMEM)
class MirrorTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_admin_log_with_organization(self):
        owner, org = make_developer(org=make_org(name='Prime'))
        member = make_membership(make_user(role='agent'), org)
        AuditLog.log(actor=owner, action=AuditLog.Action.UPDATE, target=member, detail='membership.role_changed',
                     before={'role': 'agent'}, after={'role': 'viewer'})
        e = AuditEvent.objects.get(source='core.AuditLog')
        self.assertEqual((e.category, e.action, e.actor_id, e.organization_id, e.target_type),
                         ('organization', 'membership.role_changed', str(owner.pk), str(org.pk),
                          'OrganizationMembership'))
        self.assertEqual(e.personal['after'], {'role': 'viewer'})
        AuditLog.objects.create(action=AuditLog.Action.REJECT, target_model='Lead', target_id='5', detail='Blocked!')
        self.assertEqual(AuditEvent.objects.order_by('-seq').first().action, 'lead.reject')

    def test_privacy_and_security_logs(self):
        PrivacyAuditLog.objects.create(action='export_generated', subject_identifier='abc123', details={'n': 1})
        ApiSecurityEvent.objects.create(event_type='cross_tenant', ip_address='198.51.100.7', user_id='u1',
                                        organization_id='o1', endpoint='/api/v1/leads/', threat_detail='other org')
        p, s = AuditEvent.objects.order_by('seq')
        self.assertEqual((p.category, p.action, p.target_id, p.personal), ('privacy', 'privacy.export_generated',
                                                                          'abc123', {'details': {'n': 1}}))
        self.assertEqual((s.category, s.action, s.actor_id, s.organization_id, s.meta['severity'], s.personal['ip']),
                         ('security', 'security.cross_tenant', 'u1', 'o1', 3, '198.51.100.7'))
        self.assertTrue(A.verify().ok)

    def test_a_failed_mirror_does_not_fail_the_original(self):
        with patch.object(A, 'record', side_effect=DatabaseError('down')), self.assertLogs('apps.core.audit', 'ERROR'):
            AuditLog.objects.create(action=AuditLog.Action.CONFIG, target_model='X', target_id='1')
        self.assertEqual((AuditLog.objects.count(), AuditEvent.objects.count()), (1, 0))

    def test_rolled_back_change_leaves_no_event(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            AuditLog.objects.create(action=AuditLog.Action.CONFIG, target_model='X', target_id='1')
            raise RuntimeError
        self.assertFalse(AuditEvent.objects.exists())


@override_settings(CACHES=LOCMEM)
class ApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner, self.org = make_developer(org=make_org(name='Prime'))
        self.rival, self.rival_org = make_developer(org=make_org(name='Rival'))
        A.record('membership.role_changed', category='organization', organization=self.org, actor=self.owner,
                 personal={'detail': 'agent → viewer', 'ip': '203.0.113.9'})
        A.record('security.cross_tenant', category='security', organization=self.org, personal={'ip': '1.2.3.4'})
        A.record('membership.removed', category='organization', organization=self.rival_org)
        A.record('config.changed', category='admin')

    def get(self, user, **params):
        c = APIClient()
        if user:
            c.force_authenticate(user)
        return c.get(URL, params)

    def test_platform_admin_sees_everything_and_filters(self):
        admin = make_user(role='admin')
        r = self.get(admin)
        self.assertEqual((r.status_code, r.data['count']), (200, 4))
        self.assertEqual([x['seq'] for x in r.data['results']], [4, 3, 2, 1])
        self.assertEqual(self.get(admin, category='security').data['count'], 1)
        self.assertEqual(self.get(admin, organization=str(self.org.pk)).data['count'], 2)
        self.assertEqual(self.get(admin, action='membership.removed').data['count'], 1)
        today = timezone.now().date()
        self.assertEqual(self.get(admin, **{'from': str(today), 'to': str(today)}).data['count'], 4)
        self.assertEqual(self.get(admin, **{'from': str(today + timedelta(days=1))}).data['count'], 0)
        self.assertEqual(self.get(admin, category='gossip').status_code, 400)
        self.assertEqual(self.get(admin, **{'from': 'yesterday'}).status_code, 400)
        self.assertEqual(self.get(admin).data['results'][3]['details']['ip'], '203.0.113.9')
        v = APIClient()
        v.force_authenticate(admin)
        self.assertTrue(v.get(URL + 'verify/').data['ok'])

    def test_organization_owner_sees_own_without_security_or_addresses(self):
        r = self.get(self.owner)
        self.assertEqual(r.data['count'], 1)
        row = r.data['results'][0]
        self.assertEqual((row['action'], row['details']), ('membership.role_changed', {'detail': 'agent → viewer'}))
        self.assertEqual(self.get(self.owner, organization=str(self.rival_org.pk)).data['count'], 1)   # ignored

    def test_others_are_refused(self):
        agent = make_user(role='agent')
        make_membership(agent, self.org, role=OrganizationMembership.Role.AGENT)
        for user in (agent, make_client()):
            self.assertEqual(self.get(user).status_code, 403)
        self.assertEqual(self.get(None).status_code, 401)
        c = APIClient()
        c.force_authenticate(self.owner)
        self.assertEqual(c.get(URL + 'verify/').status_code, 403)


class ConcurrencyTest(TransactionTestCase):
    """Many writers at once still make one straight chain (the old security chain could fork)."""

    def test_parallel_writers(self):
        errors = []

        def write(i):
            try:
                for j in range(5):
                    A.record('admin.update', meta={'writer': i, 'n': j})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        v = A.verify()
        self.assertEqual((v.ok, v.checked), (True, 30))
