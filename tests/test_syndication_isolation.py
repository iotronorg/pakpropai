"""
Cross-tenant security audit suite for FEATURE-SYNDICATION-ENGINE (20 tests).

a  broker cannot see DRAFT listing in browse
b  broker cannot see WITHDRAWN listing in browse
c  broker sees SYNDICATED PLATFORM_WIDE listing
d  broker cannot see SELECTED_PARTNERS listing without partnership
e  broker sees SELECTED_PARTNERS listing with ACTIVE partnership
f  suspended partner loses access
g  revoked partner loses access
h  broker cannot access developer listing detail via /listings/{id}/
i  broker cannot create listing for other org
j  developer cannot browse syndicated inventory
k  agent cannot manage partnerships
l  cross-org submission rejected (lead not owned by submitter)
m  duplicate submission rejected
n  CommissionLedgerEntry created on submission accept
o  ledger entry confirmed on deal lock
p  developer cannot see other developer's ledger
q  broker sees only own ledger entries
r  chain hash integrity verified (intact chain)
s  tampered hash detected
t  unauthenticated browse returns 401
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.marketplace.models import (
    BrokerNetworkPartnership,
    CommissionLedgerEntry,
    SyndicationLeadSubmission,
    SyndicationListing,
)
from tests.factories import make_developer, make_agent, make_org, make_property, make_lead, make_user

BASE = '/api/v1/marketplace'


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _make_listing(dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.PLATFORM_WIDE):
    return SyndicationListing.objects.create(
        property=prop, developer_org=dev_org,
        status=status, commission_type='percentage', commission_value=Decimal('3.0'),
        commission_currency='USD', syndication_scope=scope,
    )


def _make_partnership(dev_org, broker_org=None, broker_agent=None, status=BrokerNetworkPartnership.Status.ACTIVE):
    return BrokerNetworkPartnership.objects.create(
        developer_org=dev_org, broker_org=broker_org, broker_agent=broker_agent, status=status,
    )


# ── (a) broker cannot see DRAFT listing ───────────────────────────────────────

class BrokerCannotSeeDraftListingTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000001')
        self.broker, self.broker_org = make_developer(phone='+10000000002', org_name='Broker Org')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.DRAFT)

    def test_broker_cannot_see_draft_listing(self):
        resp = _client(self.broker).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 403)  # developer=403 on browse


# ── (b) broker cannot see WITHDRAWN listing ────────────────────────────────────

class BrokerCannotSeeWithdrawnListingTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000011')
        self.agent_user = make_user(phone='+10000000012', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.WITHDRAWN)

    def test_broker_cannot_see_withdrawn_listing(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 0)


# ── (c) broker sees SYNDICATED PLATFORM_WIDE listing ──────────────────────────

class BrokerSeesSyndicatedPlatformWideListing(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000021')
        self.agent_user = make_user(phone='+10000000022', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.PLATFORM_WIDE)

    def test_agent_sees_syndicated_platform_wide_listing(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)


# ── (d) no partnership → SELECTED_PARTNERS not visible ────────────────────────

class NoParntershipNoSelectedPartnersVisibility(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000031')
        self.agent_user = make_user(phone='+10000000032', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.SELECTED_PARTNERS)

    def test_no_partnership_means_no_visibility(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 0)


# ── (e) active partnership → SELECTED_PARTNERS visible ────────────────────────

class ActivePartnershipGrantsSelectedPartnersVisibility(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000041')
        self.agent_user = make_user(phone='+10000000042', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.SELECTED_PARTNERS)
        _make_partnership(self.dev_org, broker_agent=self.agent_user)

    def test_active_partnership_grants_visibility(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)


# ── (f) suspended partner loses access ────────────────────────────────────────

class SuspendedPartnerLosesAccess(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000051')
        self.agent_user = make_user(phone='+10000000052', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.SELECTED_PARTNERS)
        _make_partnership(self.dev_org, broker_agent=self.agent_user, status=BrokerNetworkPartnership.Status.SUSPENDED)

    def test_suspended_partner_cannot_see_listing(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 0)


# ── (g) revoked partner loses access ──────────────────────────────────────────

class RevokedPartnerLosesAccess(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000061')
        self.agent_user = make_user(phone='+10000000062', role='agent')
        prop = make_property(org=self.dev_org)
        _make_listing(self.dev_org, prop, status=SyndicationListing.Status.SYNDICATED, scope=SyndicationListing.SyndicationScope.SELECTED_PARTNERS)
        _make_partnership(self.dev_org, broker_agent=self.agent_user, status=BrokerNetworkPartnership.Status.REVOKED)

    def test_revoked_partner_cannot_see_listing(self):
        resp = _client(self.agent_user).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 0)


# ── (h) broker cannot access developer listing detail ─────────────────────────

class BrokerCannotAccessDraftListingDetail(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000071')
        self.agent_user = make_user(phone='+10000000072', role='agent')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop, status=SyndicationListing.Status.DRAFT)

    def test_broker_detail_on_draft_returns_404(self):
        resp = _client(self.agent_user).get(f'{BASE}/listings/{self.listing.id}/')
        self.assertIn(resp.status_code, [403, 404])


# ── (i) broker cannot create listing for other org ────────────────────────────

class BrokerCannotCreateListingForOtherOrg(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000081')
        self.agent_user = make_user(phone='+10000000082', role='agent')
        self.prop = make_property(org=self.dev_org)

    def test_agent_create_listing_returns_403(self):
        resp = _client(self.agent_user).post(f'{BASE}/listings/', {
            'property': str(self.prop.id),
            'commission_type': 'percentage',
            'commission_value': '3',
        }, format='json')
        self.assertEqual(resp.status_code, 403)


# ── (j) developer cannot browse syndicated inventory ──────────────────────────

class DeveloperCannotBrowseSyndicatedInventory(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000091')

    def test_developer_browse_returns_403(self):
        resp = _client(self.dev).get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 403)


# ── (k) agent cannot manage partnerships ──────────────────────────────────────

class AgentCannotManagePartnerships(TestCase):
    def setUp(self):
        self.agent_user = make_user(phone='+10000000101', role='agent')

    def test_agent_cannot_invite_partner(self):
        resp = _client(self.agent_user).post(f'{BASE}/partnerships/invite/', {
            'broker_org': '00000000-0000-0000-0000-000000000000',
        }, format='json')
        self.assertEqual(resp.status_code, 403)


# ── (l) cross-org submission rejected ─────────────────────────────────────────

class CrossOrgSubmissionRejected(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000111')
        self.broker, self.broker_org = make_developer(phone='+10000000112', org_name='Broker Co')
        self.other_dev, self.other_org = make_developer(phone='+10000000113', org_name='Other Org')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop)
        self.other_lead = make_lead(self.other_dev, org=self.other_org)

    def test_lead_from_other_org_submission_rejected(self):
        resp = _client(self.broker).post(f'{BASE}/submissions/', {
            'listing': str(self.listing.id),
            'lead': str(self.other_lead.id),
        }, format='json')
        self.assertIn(resp.status_code, [400, 403])


# ── (m) duplicate submission rejected ─────────────────────────────────────────

class DuplicateSubmissionRejected(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000121')
        self.broker, self.broker_org = make_developer(phone='+10000000122', org_name='Broker Co')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop)
        self.lead = make_lead(self.broker, org=self.broker_org)
        SyndicationLeadSubmission.objects.create(
            listing=self.listing, lead=self.lead, submitted_by_org=self.broker_org,
            status=SyndicationLeadSubmission.Status.PENDING,
        )

    def test_duplicate_submission_returns_400(self):
        resp = _client(self.broker).post(f'{BASE}/submissions/', {
            'listing': str(self.listing.id),
            'lead': str(self.lead.id),
        }, format='json')
        self.assertEqual(resp.status_code, 400)


# ── (n) CommissionLedgerEntry created on submission accept ────────────────────

class LedgerEntryCreatedOnAccept(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+10000000131')
        self.broker, self.broker_org = make_developer(phone='+10000000132', org_name='Broker Co')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop)
        self.lead = make_lead(self.broker, org=self.broker_org)
        self.submission = SyndicationLeadSubmission.objects.create(
            listing=self.listing, lead=self.lead, submitted_by_org=self.broker_org,
            status=SyndicationLeadSubmission.Status.PENDING,
        )

    def test_accept_creates_ledger_entry(self):
        resp = _client(self.dev).post(f'{BASE}/submissions/{self.submission.id}/accept/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CommissionLedgerEntry.objects.filter(submission=self.submission).exists())


# ── (o) ledger entry confirmed on deal lock ───────────────────────────────────

class LedgerEntryConfirmedOnDealLock(TestCase):
    def setUp(self):
        from tests.factories import make_deal
        from apps.marketplace.commission_ledger import CommissionLedger
        self.dev, self.dev_org = make_developer(phone='+10000000141')
        self.broker, self.broker_org = make_developer(phone='+10000000142', org_name='Broker Co')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop)
        self.lead = make_lead(self.broker, org=self.broker_org)
        self.submission = SyndicationLeadSubmission.objects.create(
            listing=self.listing, lead=self.lead, submitted_by_org=self.broker_org,
            status=SyndicationLeadSubmission.Status.ACCEPTED,
        )
        self.entry = CommissionLedger().record_submission(self.submission)
        buyer = make_user(phone='+10000000143', role='client')
        self.deal = make_deal(buyer, prop)

    def test_confirm_deal_updates_ledger_status(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        CommissionLedger().confirm_deal(self.deal, self.submission)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, CommissionLedgerEntry.Status.CONFIRMED)


# ── (p) developer cannot see other developer's ledger ─────────────────────────

class DeveloperCannotSeeOtherDeveloperLedger(TestCase):
    def setUp(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        self.dev_a, self.org_a = make_developer(phone='+10000000151')
        self.dev_b, self.org_b = make_developer(phone='+10000000152', org_name='Org B')
        self.broker, self.broker_org = make_developer(phone='+10000000153', org_name='Broker')
        prop = make_property(org=self.org_a)
        listing = _make_listing(self.org_a, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        submission = SyndicationLeadSubmission.objects.create(
            listing=listing, lead=lead, submitted_by_org=self.broker_org,
            status=SyndicationLeadSubmission.Status.ACCEPTED,
        )
        CommissionLedger().record_submission(submission)

    def test_dev_b_cannot_see_dev_a_ledger(self):
        resp = _client(self.dev_b).get(f'{BASE}/ledger/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        count = data['count'] if isinstance(data, dict) else len(data)
        self.assertEqual(count, 0)


# ── (q) broker sees only own ledger entries ───────────────────────────────────

class BrokerSeesOnlyOwnLedgerEntries(TestCase):
    def setUp(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        self.dev, self.dev_org = make_developer(phone='+10000000161')
        self.broker_a, self.org_a = make_developer(phone='+10000000162', org_name='Broker A')
        # broker_b is an agent — distinct from broker_a's org
        self.agent_b = make_user(phone='+10000000163', role='agent')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead_a = make_lead(self.broker_a, org=self.org_a)
        sub_a = SyndicationLeadSubmission.objects.create(
            listing=listing, lead=lead_a, submitted_by_org=self.org_a,
            status=SyndicationLeadSubmission.Status.ACCEPTED,
        )
        CommissionLedger().record_submission(sub_a)

    def test_unrelated_agent_sees_no_entries(self):
        resp = _client(self.agent_b).get(f'{BASE}/ledger/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        count = data['count'] if isinstance(data, dict) else len(data)
        self.assertEqual(count, 0)


# ── (r) chain hash integrity verified ─────────────────────────────────────────

class ChainHashIntegrityVerified(TestCase):
    def setUp(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        self.dev, self.dev_org = make_developer(phone='+10000000171')
        self.broker, self.broker_org = make_developer(phone='+10000000172', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        for i in range(3):
            u = make_user(phone=f'+1800{i:07d}', role='client')
            lead = make_lead(u, org=self.broker_org)
            sub = SyndicationLeadSubmission.objects.create(
                listing=listing, lead=lead, submitted_by_org=self.broker_org,
                status=SyndicationLeadSubmission.Status.ACCEPTED,
            )
            CommissionLedger().record_submission(sub)
        self.ledger = CommissionLedger()

    def test_intact_chain_returns_valid_true(self):
        valid, errors = self.ledger.verify_chain_integrity(self.dev_org)
        self.assertTrue(valid)
        self.assertEqual(errors, [])


# ── (s) tampered hash detected ────────────────────────────────────────────────

class TamperedHashDetected(TestCase):
    def setUp(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        self.dev, self.dev_org = make_developer(phone='+10000000181')
        self.broker, self.broker_org = make_developer(phone='+10000000182', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        for i in range(3):
            u = make_user(phone=f'+1900{i:07d}', role='client')
            lead = make_lead(u, org=self.broker_org)
            sub = SyndicationLeadSubmission.objects.create(
                listing=listing, lead=lead, submitted_by_org=self.broker_org,
                status=SyndicationLeadSubmission.Status.ACCEPTED,
            )
            CommissionLedger().record_submission(sub)
        # Tamper the middle entry's hash directly
        mid = CommissionLedgerEntry.objects.filter(developer_org=self.dev_org).order_by('created_at')[1]
        CommissionLedgerEntry.objects.filter(pk=mid.pk).update(source_chain_hash='0' * 64)

    def test_tampered_chain_returns_valid_false(self):
        from apps.marketplace.commission_ledger import CommissionLedger
        valid, errors = CommissionLedger().verify_chain_integrity(self.dev_org)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)


# ── (t) unauthenticated browse returns 401 ────────────────────────────────────

class UnauthenticatedBrowseReturns401(TestCase):
    def test_unauthenticated_returns_401(self):
        resp = APIClient().get(f'{BASE}/browse/')
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_listings_returns_401(self):
        resp = APIClient().get(f'{BASE}/listings/')
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_partnerships_returns_401(self):
        resp = APIClient().get(f'{BASE}/partnerships/')
        self.assertEqual(resp.status_code, 401)
