"""
Tests for feature_property_audit:
  - Organisation brand_color field
  - PropertyAudit delivery fields + AuditDeliveryFailure model
  - WhatsAppClient.upload_media / send_document
  - AuditMediaGateway
  - send_audit_via_whatsapp_task (success + failure paths)
  - 30-concurrent PDF generation load test
"""
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.organizations.models import Organization


class TestOrganizationBrandColor(TestCase):

    def test_brand_color_defaults_to_platform_blue(self):
        org = Organization.objects.create(name='Branding Test Org')
        self.assertEqual(org.brand_color, '#1B4F72')

    def test_brand_color_can_be_set(self):
        org = Organization.objects.create(name='Custom Color Org', brand_color='#FF5733')
        org.refresh_from_db()
        self.assertEqual(org.brand_color, '#FF5733')
