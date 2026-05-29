import logging
from dataclasses import dataclass
from django.db import transaction
from django.utils import timezone

import requests

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com/v18.0'


class ProvisioningError(Exception):
    def __init__(self, step: str, detail: str):
        self.step = step
        self.detail = detail
        super().__init__(f"Provisioning failed at {step}: {detail}")


@dataclass
class MigrationResult:
    leads_count: int
    sessions_count: int
    properties_count: int


class MetaHandshakeClient:

    def verify_waba(self, waba_id: str, access_token: str) -> dict:
        url = f"{GRAPH_API_BASE}/{waba_id}"
        try:
            resp = requests.get(
                url,
                params={'fields': 'id,name,currency,timezone_id', 'access_token': access_token},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProvisioningError('waba', f"Network error: {exc}")
        if resp.status_code != 200:
            raise ProvisioningError('waba', f"Meta API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if 'error' in data:
            raise ProvisioningError('waba', data['error'].get('message', 'Unknown Meta error'))
        return data

    def register_webhook(self, waba_id: str, access_token: str, webhook_url: str, verify_token: str) -> bool:
        url = f"{GRAPH_API_BASE}/{waba_id}/subscribed_apps"
        try:
            resp = requests.post(
                url,
                json={
                    'access_token': access_token,
                    'callback_url': webhook_url,
                    'verify_token': verify_token,
                    'fields': ['messages'],
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProvisioningError('webhook', f"Network error: {exc}")
        if resp.status_code not in (200, 201):
            data = resp.json()
            if 'error' in data and data['error'].get('code') == 100:
                return True
            raise ProvisioningError('webhook', f"Meta API {resp.status_code}: {resp.text[:200]}")
        return True

    def get_approved_templates(self, waba_id: str, access_token: str) -> list:
        url = f"{GRAPH_API_BASE}/{waba_id}/message_templates"
        try:
            resp = requests.get(
                url,
                params={'status': 'APPROVED', 'access_token': access_token},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProvisioningError('templates', f"Network error: {exc}")
        if resp.status_code != 200:
            raise ProvisioningError('templates', f"Meta API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data.get('data', [])


class SandboxDataMigrator:

    @transaction.atomic
    def migrate(self, org) -> MigrationResult:
        from apps.leads.models import Lead
        from apps.whatsapp.models import WhatsAppSession
        from apps.properties.models import Property

        leads_count = Lead.objects.filter(organization=org, is_sandbox=True).update(is_sandbox=False)
        sessions_count = WhatsAppSession.objects.filter(organization=org, is_sandbox=True).update(is_sandbox=False)
        properties_count = Property.objects.filter(organization=org, is_sandbox=True).update(is_sandbox=False)

        return MigrationResult(
            leads_count=leads_count,
            sessions_count=sessions_count,
            properties_count=properties_count,
        )


class ProductionProvisioningService:

    def __init__(self):
        self._meta_client = MetaHandshakeClient()
        self._migrator = SandboxDataMigrator()

    def run(self, org_id: str):
        from apps.organizations.models import Organization, OrgProvisioningRecord
        from apps.whatsapp.models import OrgWhatsAppConfig
        from django.core.cache import cache

        org = Organization.objects.select_related('whatsapp_config').get(id=org_id)
        record = OrgProvisioningRecord.get_or_create_for_org(org)

        try:
            wa_config = org.whatsapp_config
        except OrgWhatsAppConfig.DoesNotExist:
            self._fail(org, record, 'waba', 'OrgWhatsAppConfig not found — configure WhatsApp first')
            return record

        if not wa_config.waba_id or not wa_config.access_token:
            self._fail(org, record, 'waba', 'waba_id and access_token must be set before provisioning')
            return record

        org.operational_mode = 'provisioning'
        org.save(update_fields=['operational_mode'])
        record.operational_mode = 'provisioning'
        record.save(update_fields=['operational_mode'])

        try:
            # Step 1: WABA verification
            if not record.waba_verified_at:
                record.last_step = 'waba'
                record.save(update_fields=['last_step'])
                self._meta_client.verify_waba(wa_config.waba_id, wa_config.access_token)
                record.waba_verified_at = timezone.now()
                record.save(update_fields=['waba_verified_at'])
                logger.info("WABA verified for org %s", org_id)

            # Step 2: Webhook registration
            if not record.webhook_verified_at:
                record.last_step = 'webhook'
                record.save(update_fields=['last_step'])
                from django.conf import settings
                webhook_url = getattr(settings, 'WHATSAPP_WEBHOOK_URL', '')
                verify_token = wa_config.verify_token or ''
                self._meta_client.register_webhook(
                    wa_config.waba_id, wa_config.access_token, webhook_url, verify_token
                )
                record.webhook_verified_at = timezone.now()
                record.save(update_fields=['webhook_verified_at'])
                logger.info("Webhook registered for org %s", org_id)

            # Step 3: Template sync
            if not record.templates_approved_at:
                record.last_step = 'templates'
                record.save(update_fields=['last_step'])
                self._meta_client.get_approved_templates(wa_config.waba_id, wa_config.access_token)
                record.templates_approved_at = timezone.now()
                record.save(update_fields=['templates_approved_at'])
                logger.info("Templates synced for org %s", org_id)

            # Step 4: Data migration
            if not record.data_migrated_at:
                record.last_step = 'data'
                record.save(update_fields=['last_step'])
                result = self._migrator.migrate(org)
                record.data_migrated_at = timezone.now()
                record.sandbox_leads_migrated = result.leads_count
                record.sandbox_sessions_migrated = result.sessions_count
                record.sandbox_properties_migrated = result.properties_count
                record.save(update_fields=[
                    'data_migrated_at',
                    'sandbox_leads_migrated',
                    'sandbox_sessions_migrated',
                    'sandbox_properties_migrated',
                ])
                logger.info("Data migrated for org %s: %s", org_id, result)

            # Step 5: Go production
            record.last_step = 'complete'
            record.operational_mode = 'production'
            record.error_detail = ''
            record.save(update_fields=['last_step', 'operational_mode', 'error_detail'])

            org.operational_mode = 'production'
            org.save(update_fields=['operational_mode'])

            cache.delete(f'wa_route:{org_id}')
            cache.delete(f'org_config:{org_id}')
            logger.info("Org %s provisioned to production", org_id)

        except ProvisioningError as exc:
            self._fail(org, record, exc.step, exc.detail)

        return record

    def _fail(self, org, record, step: str, detail: str):
        logger.error("Provisioning failed for org %s at %s: %s", org.id, step, detail)
        record.operational_mode = 'failed'
        record.error_detail = detail
        record.save(update_fields=['operational_mode', 'error_detail'])
        org.operational_mode = 'failed'
        org.save(update_fields=['operational_mode'])
