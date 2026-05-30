import hashlib
import json
import logging
from decimal import Decimal

from django.db import transaction

from .models import CommissionLedgerEntry, SyndicationLeadSubmission

logger = logging.getLogger(__name__)

GENESIS = 'GENESIS'


class CommissionLedger:

    def calculate_commission(self, listing, deal_amount, partnership=None):
        """Return (commission_amount, currency)."""
        if partnership and partnership.commission_override_value is not None:
            override_type = partnership.commission_override_type
            value = partnership.commission_override_value
            currency = listing.commission_currency
        else:
            override_type = listing.commission_type
            value = listing.commission_value
            currency = listing.commission_currency

        if override_type == 'percentage':
            amount = Decimal(str(deal_amount)) * (value / Decimal('100'))
        else:
            amount = value

        return amount.quantize(Decimal('0.0001')), currency

    def _compute_entry_hash(self, entry_data):
        canonical = json.dumps(entry_data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _get_prev_hash(self, developer_org_id):
        last = (
            CommissionLedgerEntry.objects
            .filter(developer_org_id=developer_org_id)
            .order_by('-created_at')
            .values_list('source_chain_hash', flat=True)
            .first()
        )
        return last if last else GENESIS

    @transaction.atomic
    def record_submission(self, submission):
        """Create a PENDING CommissionLedgerEntry for an accepted submission."""
        listing = submission.listing
        partnership = _resolve_partnership(submission)
        deal_amount = submission.commission_calculated or Decimal('0')
        commission_amount, commission_currency = self.calculate_commission(
            listing, deal_amount, partnership
        )

        developer_org = listing.developer_org
        broker_org = submission.submitted_by_org
        broker_agent = submission.submitted_by_agent

        import uuid
        entry_id = uuid.uuid4()
        prev_hash = self._get_prev_hash(developer_org.pk)

        entry_data = {
            'entry_id': str(entry_id),
            'listing_id': str(listing.pk),
            'submission_id': str(submission.pk),
            'developer_org_id': str(developer_org.pk),
            'broker_org_id': str(broker_org.pk) if broker_org else None,
            'broker_agent_id': str(broker_agent.pk) if broker_agent else None,
            'commission_amount': str(commission_amount),
            'commission_currency': commission_currency,
            'commission_type': listing.commission_type,
        }
        source_hash = self._compute_entry_hash(entry_data)

        return CommissionLedgerEntry.objects.create(
            entry_id=entry_id,
            listing=listing,
            submission=submission,
            developer_org=developer_org,
            broker_org=broker_org,
            broker_agent=broker_agent,
            commission_amount=commission_amount,
            commission_currency=commission_currency,
            commission_type=listing.commission_type,
            source_chain_hash=source_hash,
            prev_entry_hash=prev_hash,
            status=CommissionLedgerEntry.Status.PENDING,
        )

    def confirm_deal(self, deal_lock, submission):
        """Find the PENDING ledger entry for submission and mark it CONFIRMED."""
        entry = (
            CommissionLedgerEntry.objects
            .filter(submission=submission, status=CommissionLedgerEntry.Status.PENDING)
            .first()
        )
        if entry is None:
            return None
        entry.status = CommissionLedgerEntry.Status.CONFIRMED
        entry.deal_lock = deal_lock
        # Use raw queryset update to bypass the delete() guard (update is fine)
        CommissionLedgerEntry.objects.filter(pk=entry.pk).update(
            status=CommissionLedgerEntry.Status.CONFIRMED,
            deal_lock=deal_lock,
        )
        entry.refresh_from_db()
        return entry

    def verify_chain_integrity(self, developer_org):
        """Re-compute each entry's hash and verify the prev_entry_hash chain."""
        entries = list(
            CommissionLedgerEntry.objects
            .filter(developer_org=developer_org)
            .order_by('created_at')
        )
        errors = []
        prev_hash = GENESIS

        for entry in entries:
            entry_data = {
                'entry_id': str(entry.entry_id),
                'listing_id': str(entry.listing_id),
                'submission_id': str(entry.submission_id),
                'developer_org_id': str(entry.developer_org_id),
                'broker_org_id': str(entry.broker_org_id) if entry.broker_org_id else None,
                'broker_agent_id': str(entry.broker_agent_id) if entry.broker_agent_id else None,
                'commission_amount': str(entry.commission_amount),
                'commission_currency': entry.commission_currency,
                'commission_type': entry.commission_type,
            }
            expected_hash = self._compute_entry_hash(entry_data)

            if entry.source_chain_hash != expected_hash:
                errors.append(f'Entry {entry.entry_id}: hash mismatch (stored={entry.source_chain_hash[:8]}...)')

            if entry.prev_entry_hash != prev_hash:
                errors.append(f'Entry {entry.entry_id}: prev_hash mismatch (expected={prev_hash[:8]}..., stored={entry.prev_entry_hash[:8]}...)')

            prev_hash = entry.source_chain_hash

        return (len(errors) == 0, errors)


def _resolve_partnership(submission):
    """Find the active BrokerNetworkPartnership for this submission's broker."""
    from .models import BrokerNetworkPartnership
    from django.db.models import Q
    q = Q(developer_org=submission.listing.developer_org, status=BrokerNetworkPartnership.Status.ACTIVE)
    if submission.submitted_by_org:
        q &= Q(broker_org=submission.submitted_by_org)
    elif submission.submitted_by_agent:
        q &= Q(broker_agent=submission.submitted_by_agent)
    else:
        return None
    return BrokerNetworkPartnership.objects.filter(q).first()
