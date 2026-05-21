import hashlib
import hmac
import json
import threading

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.agents.models import Agent
from apps.escrow.models import EscrowDeal
from apps.organizations.models import Organization
from apps.properties.models import Property

User = get_user_model()

_BSECURE_TEST_SECRET = 'test-bsecure-secret'


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _user(phone='+923001234567', role='client'):
    return User.objects.create_user(phone=phone, password='pw', role=role)


def _property(title='Test Property'):
    """Platform-owned — owner=None so _notify_seller_lock_initiated returns early."""
    return Property.objects.create(
        listing_owner_type='platform',
        title=title,
        city='Lahore',
        location='DHA Phase 5',
        property_type='residential',
        price=5_000_000,
        area_marla=5,
    )


def _deal(buyer, prop, agent=None):
    return EscrowDeal.objects.create(
        buyer=buyer,
        property=prop,
        agent=agent,
        token_amount=25_000,
        status=EscrowDeal.Status.INITIATED,
    )


def _bsecure_webhook_post(client, payload, secret=_BSECURE_TEST_SECRET):
    body = json.dumps(payload, separators=(',', ':')).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse('webhook-bsecure'),
        data=body,
        content_type='application/json',
        HTTP_X_BSECURE_SIGNATURE=sig,
    )


# ── Task 1: Concurrency ────────────────────────────────────────────────────────

class DealLockConcurrencyTest(TransactionTestCase):
    """Two buyers race to lock the same property; exactly one must succeed."""

    def setUp(self):
        self.buyer1 = _user(phone='+923001000001')
        self.buyer2 = _user(phone='+923001000002')
        self.prop   = _property()

    def test_concurrent_lock_attempts_one_succeeds_one_400(self):
        results = [None, None]
        barrier = threading.Barrier(2)

        def attempt(user, idx):
            client = APIClient()
            client.force_authenticate(user=user)
            barrier.wait()           # both threads hit the endpoint simultaneously
            resp = client.post(
                reverse('deal-initiate'),
                {
                    'property_id':     str(self.prop.pk),
                    'token_amount':    25_000,
                    'payment_gateway': 'jazzcash',
                },
                format='json',
            )
            results[idx] = resp.status_code

        threads = [
            threading.Thread(target=attempt, args=(self.buyer1, 0)),
            threading.Thread(target=attempt, args=(self.buyer2, 1)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertIn(status.HTTP_201_CREATED,     results, "One request must succeed")
        self.assertIn(status.HTTP_400_BAD_REQUEST,  results, "Loser must get a clean 400, not 500")
        self.assertEqual(
            EscrowDeal.objects.filter(property=self.prop).count(), 1,
            "Exactly one deal must be created"
        )


# ── Task 2a: Initiation validation ────────────────────────────────────────────

class DealLockInitiateViewTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer  = _user()
        self.prop   = _property()

    def test_happy_path_returns_201_with_deal_id(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', resp.data)
        self.assertEqual(resp.data['status'], EscrowDeal.Status.INITIATED)

    def test_duplicate_lock_returns_400_with_message(self):
        _deal(buyer=self.buyer, prop=self.prop)   # existing active lock
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('active deal lock', str(resp.data).lower())

    def test_inactive_property_returns_400(self):
        self.prop.is_active = False
        self.prop.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_below_minimum_returns_400(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 24_999, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_above_maximum_returns_400(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 100_001, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_returns_401(self):
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ── Task 2b: bSecure webhook ───────────────────────────────────────────────────

@override_settings(BSECURE_CLIENT_SECRET=_BSECURE_TEST_SECRET)
class bSecureWebhookTest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer  = _user()
        self.prop   = _property()
        self.deal   = _deal(buyer=self.buyer, prop=self.prop)

    def _success_payload(self, order_id=None):
        return {
            'status': 'paid',
            'order': {
                'order_ref': str(order_id or self.deal.id),
                'amount':    25_000,
            },
            'transaction_ref': 'TRK-bsec123',
        }

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_valid_webhook_activates_deal(self, mock_notify):
        resp = _bsecure_webhook_post(self.client, self._success_payload())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)
        mock_notify.assert_called_once()

    def test_invalid_hmac_not_activated(self):
        body = json.dumps(self._success_payload(), separators=(',', ':')).encode()
        resp = self.client.post(
            reverse('webhook-bsecure'),
            data=body,
            content_type='application/json',
            HTTP_X_BSECURE_SIGNATURE='deadbeef',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertNotEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    def test_failed_payment_does_not_activate(self):
        payload = {
            'status': 'failed',
            'order':  {'order_ref': str(self.deal.id), 'amount': 25_000},
            'transaction_ref': 'TRK-bsec456',
        }
        resp = _bsecure_webhook_post(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertNotEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    @patch('apps.escrow.views._notify_buyer_lock_active')
    def test_duplicate_webhook_is_idempotent(self, _):
        self.deal.activate_lock()     # already locked
        resp = _bsecure_webhook_post(self.client, self._success_payload())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.status, EscrowDeal.Status.LOCKED)

    def test_unknown_order_id_not_activated(self):
        import uuid
        resp = _bsecure_webhook_post(self.client, self._success_payload(order_id=uuid.uuid4()))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['activated'])


# ── Task 2c: Org isolation ─────────────────────────────────────────────────────

class OrgIsolationTest(TestCase):
    """Locking a property under Org A must never affect Org B inventory or deals."""

    def _org_setup(self, suffix):
        admin = _user(phone=f'+923002{suffix}0001', role='developer')
        org   = Organization.objects.create(name=f'Org {suffix}', admin_user=admin)
        agent = Agent.objects.create(
            name=f'Agent {suffix}',
            phone=f'+923002{suffix}0002',
            organization=org,
            employment_type='internal',
        )
        prop = Property.objects.create(
            listing_owner_type='organization',
            organization=org,
            title='Identical Property Title',
            city='Karachi',
            location='Clifton',
            property_type='residential',
            price=8_000_000,
            area_marla=10,
        )
        return admin, org, agent, prop

    def setUp(self):
        self.client = APIClient()
        self.admin_a, self.org_a, self.agent_a, self.prop_a = self._org_setup('A')
        self.admin_b, self.org_b, self.agent_b, self.prop_b = self._org_setup('B')
        self.buyer = _user(phone='+923009999999')
        # Lock only Org A's property, assign Org A's agent so list scoping works
        self.deal_a = EscrowDeal.objects.create(
            buyer=self.buyer,
            property=self.prop_a,
            agent=self.agent_a,
            token_amount=25_000,
            status=EscrowDeal.Status.INITIATED,
        )

    def test_org_b_property_has_no_active_deal(self):
        self.assertFalse(
            EscrowDeal.objects.filter(
                property=self.prop_b,
                status__in=[EscrowDeal.Status.INITIATED, EscrowDeal.Status.LOCKED],
            ).exists(),
            "Org B's property must have no active deal lock"
        )

    def test_org_b_property_remains_active(self):
        self.prop_b.refresh_from_db()
        self.assertTrue(self.prop_b.is_active)

    def test_developer_a_list_sees_only_org_a_deals(self):
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get(reverse('deal-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        ids = [d['id'] for d in results]
        self.assertIn(str(self.deal_a.id), ids)
        self.assertEqual(len(ids), 1, "Developer A must see exactly one deal")

    def test_developer_b_list_sees_no_deals(self):
        self.client.force_authenticate(user=self.admin_b)
        resp = self.client.get(reverse('deal-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        self.assertEqual(len(results), 0, "Developer B must see zero deals")


# ── Task 2d: Org payment settings override ────────────────────────────────────

class OrgPaymentSettingsOverrideTest(TestCase):
    """Deal lock uses org-level payment config when available."""

    def setUp(self):
        from apps.organizations.models import OrgPaymentSettings
        self.client = APIClient()
        self.buyer  = _user(phone='+923008880001')

        self.admin = _user(phone='+923008880002', role='developer')
        self.org   = Organization.objects.create(name='Payment Org', admin_user=self.admin)

        self.prop  = Property.objects.create(
            listing_owner_type='organization',
            organization=self.org,
            title='Org Property',
            city='Lahore',
            location='DHA Phase 5',
            property_type='residential',
            price=5_000_000,
            area_marla=5,
        )

        self.ps, _ = OrgPaymentSettings.objects.get_or_create(organization=self.org)

    def test_org_jazzcash_number_appears_in_payment_message(self):
        self.ps.gateway        = 'manual'
        self.ps.jazzcash_number = '0300-1112233'
        self.ps.save(update_fields=['gateway', 'jazzcash_number'])

        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('0300-1112233', resp.data['payment_message'])

    def test_org_safepay_gateway_overrides_user_choice(self):
        self.ps.gateway = 'safepay'
        self.ps.save(update_fields=['gateway'])

        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'jazzcash'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['payment_gateway'], 'safepay')

    def test_org_manual_gateway_preserves_user_choice(self):
        self.ps.gateway = 'manual'
        self.ps.save(update_fields=['gateway'])

        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            reverse('deal-initiate'),
            {'property_id': str(self.prop.pk), 'token_amount': 25_000, 'payment_gateway': 'easypaisa'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['payment_gateway'], 'easypaisa')
