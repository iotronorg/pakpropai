"""
Commission Ledger correctness tests (14 tests).

a  percentage commission calculated correctly
b  fixed commission stored verbatim
c  partnership override takes precedence over listing rate
d  no override uses listing rate
e  genesis hash on first entry for new developer org
f  prev_hash links to prior source_chain_hash
g  source_chain_hash deterministic (same data → same hash)
h  record_submission is atomic — DB error rolls back fully
i  confirm_deal sets status CONFIRMED + deal_lock
j  confirm_deal is no-op when no submission found
k  verify_chain_integrity detects hash mutation
l  verify_chain_integrity on empty ledger returns (True, [])
m  multi-org ledger isolation — verify_chain for org A validates only org A chain
n  CommissionLedgerEntry.delete() raises PermissionError
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.marketplace.commission_ledger import CommissionLedger, GENESIS
from apps.marketplace.models import (
    BrokerNetworkPartnership,
    CommissionLedgerEntry,
    SyndicationLeadSubmission,
    SyndicationListing,
)
from tests.factories import make_developer, make_property, make_lead, make_user, make_deal


def _make_listing(dev_org, prop, commission_type='percentage', commission_value='3.0', currency='USD'):
    return SyndicationListing.objects.create(
        property=prop, developer_org=dev_org,
        status=SyndicationListing.Status.SYNDICATED,
        commission_type=commission_type,
        commission_value=Decimal(commission_value),
        commission_currency=currency,
        syndication_scope=SyndicationListing.SyndicationScope.PLATFORM_WIDE,
    )


def _make_submission(listing, lead, submitted_by_org, status=SyndicationLeadSubmission.Status.ACCEPTED):
    return SyndicationLeadSubmission.objects.create(
        listing=listing, lead=lead, submitted_by_org=submitted_by_org,
        status=status,
    )


class PercentageCommissionCalculatedCorrectlyTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000001')
        self.broker, self.broker_org = make_developer(phone='+20000000002', org_name='Broker')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop, commission_type='percentage', commission_value='3.0')

    def test_percentage_commission(self):
        amount, currency = CommissionLedger().calculate_commission(
            self.listing, deal_amount=Decimal('200000'), partnership=None
        )
        self.assertEqual(amount, Decimal('6000.0000'))
        self.assertEqual(currency, 'USD')


class FixedCommissionStoredVerbatimTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000011')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop, commission_type='fixed', commission_value='5000')

    def test_fixed_commission(self):
        amount, currency = CommissionLedger().calculate_commission(
            self.listing, deal_amount=Decimal('200000'), partnership=None
        )
        self.assertEqual(amount, Decimal('5000.0000'))


class PartnershipOverrideTakesPrecedenceTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000021')
        self.broker, self.broker_org = make_developer(phone='+20000000022', org_name='Broker')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop, commission_type='percentage', commission_value='3.0')
        self.partnership = BrokerNetworkPartnership.objects.create(
            developer_org=self.dev_org,
            broker_org=self.broker_org,
            status=BrokerNetworkPartnership.Status.ACTIVE,
            commission_override_type='percentage',
            commission_override_value=Decimal('2.0'),
        )

    def test_override_applies(self):
        amount, _ = CommissionLedger().calculate_commission(
            self.listing, Decimal('200000'), partnership=self.partnership
        )
        self.assertEqual(amount, Decimal('4000.0000'))  # 2% of 200000


class NoOverrideUsesListingRateTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000031')
        self.broker, self.broker_org = make_developer(phone='+20000000032', org_name='Broker')
        prop = make_property(org=self.dev_org)
        self.listing = _make_listing(self.dev_org, prop, commission_type='percentage', commission_value='3.0')
        self.partnership = BrokerNetworkPartnership.objects.create(
            developer_org=self.dev_org, broker_org=self.broker_org,
            status=BrokerNetworkPartnership.Status.ACTIVE,
            commission_override_value=None,
        )

    def test_listing_rate_used_when_no_override(self):
        amount, _ = CommissionLedger().calculate_commission(
            self.listing, Decimal('200000'), partnership=self.partnership
        )
        self.assertEqual(amount, Decimal('6000.0000'))  # 3% of 200000


class GenesisHashOnFirstEntryTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000041')
        self.broker, self.broker_org = make_developer(phone='+20000000042', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        self.submission = _make_submission(listing, lead, self.broker_org)

    def test_first_entry_prev_hash_is_genesis(self):
        entry = CommissionLedger().record_submission(self.submission)
        self.assertEqual(entry.prev_entry_hash, GENESIS)


class PrevHashLinksToLastSourceChainHashTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000051')
        self.broker, self.broker_org = make_developer(phone='+20000000052', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        user1 = make_user(phone='+20000000053', role='client')
        user2 = make_user(phone='+20000000054', role='client')
        lead1 = make_lead(user1, org=self.broker_org)
        lead2 = make_lead(user2, org=self.broker_org)
        self.sub1 = _make_submission(listing, lead1, self.broker_org)
        self.sub2 = _make_submission(listing, lead2, self.broker_org)

    def test_second_entry_prev_hash_matches_first_source_hash(self):
        ledger = CommissionLedger()
        entry1 = ledger.record_submission(self.sub1)
        entry2 = ledger.record_submission(self.sub2)
        self.assertEqual(entry2.prev_entry_hash, entry1.source_chain_hash)


class SourceChainHashDeterministicTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000061')
        self.broker, self.broker_org = make_developer(phone='+20000000062', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        self.submission = _make_submission(listing, lead, self.broker_org)

    def test_same_entry_data_produces_same_hash(self):
        import uuid
        ledger = CommissionLedger()
        entry_id = str(uuid.uuid4())
        data = {
            'entry_id': entry_id,
            'listing_id': 'aaa',
            'submission_id': 'bbb',
            'developer_org_id': 'ccc',
            'broker_org_id': 'ddd',
            'broker_agent_id': None,
            'commission_amount': '6000.0000',
            'commission_currency': 'USD',
            'commission_type': 'percentage',
        }
        self.assertEqual(ledger._compute_entry_hash(data), ledger._compute_entry_hash(data))


class RecordSubmissionAtomicTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000071')
        self.broker, self.broker_org = make_developer(phone='+20000000072', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        self.submission = _make_submission(listing, lead, self.broker_org)

    def test_db_error_on_create_rolls_back(self):
        from django.db import IntegrityError
        with patch.object(CommissionLedgerEntry.objects, 'create', side_effect=IntegrityError('fail')):
            with self.assertRaises(IntegrityError):
                CommissionLedger().record_submission(self.submission)
        self.assertEqual(CommissionLedgerEntry.objects.filter(submission=self.submission).count(), 0)


class ConfirmDealSetsConfirmedTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000081')
        self.broker, self.broker_org = make_developer(phone='+20000000082', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        self.submission = _make_submission(listing, lead, self.broker_org)
        self.entry = CommissionLedger().record_submission(self.submission)
        buyer = make_user(phone='+20000000083', role='client')
        self.deal = make_deal(buyer, prop)

    def test_confirm_deal_sets_status_confirmed(self):
        CommissionLedger().confirm_deal(self.deal, self.submission)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, CommissionLedgerEntry.Status.CONFIRMED)
        self.assertEqual(self.entry.deal_lock, self.deal)


class ConfirmDealNoOpWhenNoSubmissionTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000091')
        buyer = make_user(phone='+20000000092', role='client')
        prop = make_property(org=self.dev_org)
        self.deal = make_deal(buyer, prop)
        self.broker, self.broker_org = make_developer(phone='+20000000093', org_name='Broker')
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        self.submission = _make_submission(listing, lead, self.broker_org)

    def test_confirm_no_pending_entry_returns_none(self):
        result = CommissionLedger().confirm_deal(self.deal, self.submission)
        self.assertIsNone(result)


class VerifyChainIntegrityDetectsMutationTest(TestCase):
    def setUp(self):
        from apps.marketplace.commission_ledger import CommissionLedger as CL
        self.dev, self.dev_org = make_developer(phone='+20000000101')
        self.broker, self.broker_org = make_developer(phone='+20000000102', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        for i in range(3):
            u = make_user(phone=f'+2011{i:08d}', role='client')
            lead = make_lead(u, org=self.broker_org)
            sub = _make_submission(listing, lead, self.broker_org)
            CL().record_submission(sub)
        mid = CommissionLedgerEntry.objects.filter(developer_org=self.dev_org).order_by('created_at')[1]
        CommissionLedgerEntry.objects.filter(pk=mid.pk).update(source_chain_hash='f' * 64)

    def test_detects_tampered_hash(self):
        valid, errors = CommissionLedger().verify_chain_integrity(self.dev_org)
        self.assertFalse(valid)
        self.assertGreater(len(errors), 0)


class VerifyEmptyLedgerIsValidTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000111')

    def test_empty_ledger_is_valid(self):
        valid, errors = CommissionLedger().verify_chain_integrity(self.dev_org)
        self.assertTrue(valid)
        self.assertEqual(errors, [])


class MultiOrgLedgerIsolationTest(TestCase):
    def setUp(self):
        self.dev_a, self.org_a = make_developer(phone='+20000000121')
        self.dev_b, self.org_b = make_developer(phone='+20000000122', org_name='Org B')
        self.broker, self.broker_org = make_developer(phone='+20000000123', org_name='Broker')
        prop_a = make_property(org=self.org_a)
        listing_a = _make_listing(self.org_a, prop_a)
        for i in range(3):
            u = make_user(phone=f'+2021{i:08d}', role='client')
            lead = make_lead(u, org=self.broker_org)
            sub = _make_submission(listing_a, lead, self.broker_org)
            CommissionLedger().record_submission(sub)

    def test_verify_org_a_validates_only_org_a_entries(self):
        valid, errors = CommissionLedger().verify_chain_integrity(self.org_a)
        self.assertTrue(valid)
        self.assertEqual(errors, [])
        # Org B has no entries — also valid
        valid_b, _ = CommissionLedger().verify_chain_integrity(self.org_b)
        self.assertTrue(valid_b)


class LedgerEntryDeleteRaisesPermissionErrorTest(TestCase):
    def setUp(self):
        self.dev, self.dev_org = make_developer(phone='+20000000131')
        self.broker, self.broker_org = make_developer(phone='+20000000132', org_name='Broker')
        prop = make_property(org=self.dev_org)
        listing = _make_listing(self.dev_org, prop)
        lead = make_lead(self.broker, org=self.broker_org)
        sub = _make_submission(listing, lead, self.broker_org)
        self.entry = CommissionLedger().record_submission(sub)

    def test_delete_raises_permission_error(self):
        with self.assertRaises(PermissionError):
            self.entry.delete()
