import logging
from dataclasses import dataclass

from apps.config.services import SystemConfigService

logger = logging.getLogger(__name__)

_REGION_TO_DB: dict[str, str] = {
    'eu':     'eu_cluster',
    'uk':     'uk_cluster',
    'uae':    'uae_cluster',
    'pk':     'pk_cluster',
    'global': 'default',
}

# Countries considered adequate for GDPR data transfers (no SCC needed)
_GDPR_ADEQUATE_COUNTRIES = {'UK', 'AE', 'CA', 'JP', 'IL', 'NZ', 'CH', 'KR'}

# AI regions that serve EU-resident data (excludes US-only endpoints)
_EU_ALLOWED_AI_REGIONS = ['eu-west', 'eu-central']
_DEFAULT_AI_REGIONS = ['us-central', 'eu-west', 'eu-central', 'ap-southeast']


@dataclass
class TransferDecision:
    allowed: bool
    reason: str


class RegionalDataRouter:
    """Maps org data-residency config to infrastructure routing decisions."""

    def get_db_alias(self, org) -> str:
        try:
            region = (org.data_residency_region or 'global').lower()
            return _REGION_TO_DB.get(region, 'default')
        except Exception:
            logger.warning("RegionalDataRouter.get_db_alias failed, falling back to default", exc_info=True)
            return 'default'

    def get_s3_bucket(self, org) -> str:
        try:
            region = (org.data_residency_region or 'global').lower()
            bucket = SystemConfigService.get(f's3_bucket_{region}')
            if bucket:
                return bucket
            return SystemConfigService.get('s3_bucket_default', default='realtron-default')
        except Exception:
            logger.warning("RegionalDataRouter.get_s3_bucket failed, using default", exc_info=True)
            return SystemConfigService.get('s3_bucket_default', default='realtron-default')

    def get_allowed_ai_regions(self, org) -> list[str]:
        try:
            region = (org.data_residency_region or 'global').lower()
            if region == 'eu':
                return _EU_ALLOWED_AI_REGIONS
            return _DEFAULT_AI_REGIONS
        except Exception:
            logger.warning("RegionalDataRouter.get_allowed_ai_regions failed", exc_info=True)
            return _DEFAULT_AI_REGIONS

    def validate_transfer(self, org, destination_region: str) -> TransferDecision:
        """
        GDPR: EU orgs may not transfer to non-adequate countries unless
        SystemConfig key privacy_eu_scc_accepted == 'true'.
        Non-GDPR orgs always pass.
        """
        try:
            from apps.compliance.utils import org_is_gdpr_jurisdiction
            if not org_is_gdpr_jurisdiction(org):
                return TransferDecision(allowed=True, reason="Non-GDPR jurisdiction — transfer permitted")

            dest = destination_region.upper()
            if dest in _GDPR_ADEQUATE_COUNTRIES:
                return TransferDecision(allowed=True, reason=f"{dest} is an adequate country under GDPR")

            scc_accepted = SystemConfigService.get('privacy_eu_scc_accepted', default='false')
            if scc_accepted.lower() == 'true':
                return TransferDecision(allowed=True, reason="EU SCCs accepted — transfer permitted")

            return TransferDecision(
                allowed=False,
                reason=f"GDPR: transfer to {dest} blocked — not an adequate country and SCCs not accepted",
            )
        except Exception:
            logger.warning("RegionalDataRouter.validate_transfer failed, allowing transfer", exc_info=True)
            return TransferDecision(allowed=True, reason="Validation error — fail-open")
