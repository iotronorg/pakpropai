"""
T-1040 (KI-017) — ``is_sandbox`` for leads and WhatsApp sessions follows the
organization: derived on insert and whenever the organization changes
(``update_fields``-aware); no organization → live. The org's own go-live is the
provisioner's job. Backfills 0021 / 0013.
"""
import importlib

from django.test import TestCase

from apps.leads.models import Lead
from apps.organizations.sandbox import contact_is_sandbox
from apps.whatsapp.models import WhatsAppSession
from tests.factories import make_agent, make_client, make_lead, make_org


def _prod(name='Prod'):
    return make_org(name=name, operational_mode='production')


class LeadFollowsOrgTest(TestCase):
    def test_insert_ignores_caller_flag(self):
        self.assertFalse(make_lead(make_client(), is_sandbox=True).is_sandbox)
        self.assertTrue(make_lead(make_client(), org=make_org(), is_sandbox=False).is_sandbox)

    def test_org_assigned_later_rederives_with_update_fields(self):
        from apps.leads.utils import upsert_lead
        user = make_client()
        lead = make_lead(user)
        upsert_lead(user, 'buy', organization=make_org(name='Sbx'))   # update_fields path
        lead.refresh_from_db()
        self.assertTrue(lead.is_sandbox)

    def test_assign_agent_moves_lead_into_production_org(self):
        from apps.leads.services import assign_agent_to_lead
        org = _prod()
        _, agent = make_agent(org=org)
        lead = make_lead(make_client(), org=None)
        lead = Lead.objects.get(pk=lead.pk)                           # loaded from the DB
        assign_agent_to_lead(lead, agent)
        lead.refresh_from_db()
        self.assertEqual((lead.organization_id, lead.is_sandbox), (org.pk, False))

    def test_unrelated_saves_and_org_mode_changes_do_not_touch_it(self):
        org = make_org()
        lead = Lead.objects.get(pk=make_lead(make_client(), org=org).pk)
        org.operational_mode = 'production'
        org.save(update_fields=['operational_mode'])
        lead.notes = 'x'
        lead.save()
        lead.save(update_fields=['notes'])
        lead.refresh_from_db()
        self.assertTrue(lead.is_sandbox)                              # provisioner flips it

    def test_removing_org_makes_it_live_and_deferred_load_is_safe(self):
        lead = Lead.objects.get(pk=make_lead(make_client(), org=make_org()).pk)
        lead.organization = None
        lead.save()
        lead.refresh_from_db()
        self.assertFalse(lead.is_sandbox)
        deferred = Lead.objects.only('id', 'notes').get(pk=lead.pk)
        deferred.notes = 'y'
        deferred.save(update_fields=['notes'])                       # org not loaded → untouched
        self.assertFalse(Lead.objects.get(pk=lead.pk).is_sandbox)


class SessionFollowsOrgTest(TestCase):
    def test_session_org_change(self):
        s = WhatsAppSession.objects.create(phone='+923001110001')
        self.assertFalse(s.is_sandbox)
        s = WhatsAppSession.objects.get(pk=s.pk)
        s.organization = make_org()
        s.save(update_fields=['organization'])
        s.refresh_from_db()
        self.assertTrue(s.is_sandbox)
        s.organization = _prod()
        s.save(update_fields=['organization'])
        s.refresh_from_db()
        self.assertFalse(s.is_sandbox)

    def test_helper(self):
        self.assertFalse(contact_is_sandbox(None))
        self.assertTrue(contact_is_sandbox(make_org().pk))
        self.assertFalse(contact_is_sandbox(_prod().pk))


class BackfillTest(TestCase):
    def test_backfills(self):
        from django.db import connection
        prod, sbx = _prod(), make_org(name='S')
        unscoped = make_lead(make_client())
        late = make_lead(make_client(), org=prod)
        keep = make_lead(make_client(), org=sbx)
        Lead.objects.filter(pk__in=[unscoped.pk, late.pk]).update(is_sandbox=True)   # pre-T-1040 rows
        sess = WhatsAppSession.objects.create(phone='+923001110002')
        WhatsAppSession.objects.filter(pk=sess.pk).update(is_sandbox=True)

        class Editor:
            pass
        editor = Editor()
        editor.connection = connection
        importlib.import_module('apps.leads.migrations.0021_unscoped_leads_live').backfill(None, editor)
        importlib.import_module('apps.whatsapp.migrations.0013_unscoped_sessions_live').backfill(None, editor)
        flags = dict(Lead.objects.values_list('pk', 'is_sandbox'))
        self.assertEqual((flags[unscoped.pk], flags[late.pk], flags[keep.pk]), (False, False, True))
        self.assertFalse(WhatsAppSession.objects.get(pk=sess.pk).is_sandbox)
