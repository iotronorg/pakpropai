"""
Tests for GLOBAL-2: AML sanction list sync (OFAC, UN, EU).

8 tests:
  1. OFAC XML parsed correctly
  2. UN XML parsed correctly
  3. EU XML parsed correctly
  4. Normalization strips diacritics
  5. Upsert is idempotent (run twice → no duplicate records)
  6. Deactivates entries removed from the list
  7. Management command outputs summary and exits 0
  8. sync_aml_sanction_lists Celery task is in the beat schedule
"""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.compliance.management.commands.sync_sanctions import (
    _normalize,
    _parse_eu,
    _parse_ofac,
    _parse_un,
    sync_list,
)
from apps.compliance.models import ComplianceSanctionRecord

# ── Minimal XML fixtures ───────────────────────────────────────────────────────

OFAC_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sdnList>
  <sdnEntry>
    <lastName>SMITH</lastName>
    <firstName>John</firstName>
    <idList>
      <id><idNumber>AB123456</idNumber></id>
    </idList>
  </sdnEntry>
  <sdnEntry>
    <lastName>BLOFELD</lastName>
    <firstName></firstName>
  </sdnEntry>
</sdnList>"""

UN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <FIRST_NAME>Ahmad</FIRST_NAME>
      <SECOND_NAME>Al-Rashidi</SECOND_NAME>
      <THIRD_NAME></THIRD_NAME>
      <INDIVIDUAL_DOCUMENT>
        <TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT>
        <NUMBER>FE999888</NUMBER>
      </INDIVIDUAL_DOCUMENT>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY>
      <FIRST_NAME>AL-QAIDA NETWORK</FIRST_NAME>
    </ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>"""

EU_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<export>
  <sanctionEntity logicalId="ame.001">
    <nameAlias regulationLanguage="eng"
               fullName="Vladimir Petrov"
               firstName="Vladimir"
               lastName="Petrov"/>
    <identification documentType="PASSPORT" number="RU123456"/>
  </sanctionEntity>
  <sanctionEntity logicalId="ame.002">
    <nameAlias regulationLanguage="eng"
               fullName=""
               wholeName=""
               firstName="Elena"
               lastName="Morozova"/>
  </sanctionEntity>
</export>"""


def _mock_response(content: bytes):
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


class OfacParseTest(TestCase):
    def test_ofac_xml_parsed_correctly(self):
        """OFAC XML: individual with firstName+lastName and entity with lastName only."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(OFAC_XML)
        entries = _parse_ofac(root)
        names = [e[0] for e in entries]
        self.assertIn('john smith', [_normalize(n) for n in names])
        self.assertIn('blofeld',    [_normalize(n) for n in names])
        # ID number captured for first entry
        ids = {_normalize(e[0]): e[1] for e in entries}
        self.assertEqual(ids.get('john smith'), 'AB123456')


class UnParseTest(TestCase):
    def test_un_xml_parsed_correctly(self):
        """UN XML: individual with multi-part name + document, entity with name."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(UN_XML)
        entries = _parse_un(root)
        names = [_normalize(e[0]) for e in entries]
        self.assertIn('ahmad al-rashidi', names)
        self.assertIn('al-qaida network', names)
        ids = {_normalize(e[0]): e[1] for e in entries}
        self.assertEqual(ids.get('ahmad al-rashidi'), 'FE999888')


class EuParseTest(TestCase):
    def test_eu_xml_parsed_correctly(self):
        """EU XML: fullName attribute and firstName/lastName fallback both captured."""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(EU_XML)
        entries = _parse_eu(root)
        names = [_normalize(e[0]) for e in entries]
        self.assertIn('vladimir petrov', names)
        self.assertIn('elena morozova', names)


class NormalizationTest(TestCase):
    def test_normalize_strips_diacritics(self):
        """_normalize() removes combining diacritics and lowercases."""
        self.assertEqual(_normalize('José Müller'),   'jose muller')
        self.assertEqual(_normalize('Ğumar Çelik'),   'gumar celik')
        self.assertEqual(_normalize('Rémi Lefèvre'),  'remi lefevre')
        self.assertEqual(_normalize('  Iván  GARCÍA  '), 'ivan garcia')


class UpsertIdempotencyTest(TestCase):
    def test_upsert_is_idempotent(self):
        """Running sync_list twice produces no duplicate records."""
        with patch('requests.get', return_value=_mock_response(OFAC_XML)):
            sync_list('OFAC', 'https://example.com/sdn.xml')
            count_after_first = ComplianceSanctionRecord.objects.filter(list_source='OFAC').count()

        with patch('requests.get', return_value=_mock_response(OFAC_XML)):
            sync_list('OFAC', 'https://example.com/sdn.xml')
            count_after_second = ComplianceSanctionRecord.objects.filter(list_source='OFAC').count()

        self.assertEqual(count_after_first, count_after_second)
        self.assertGreater(count_after_first, 0)


class DeactivationTest(TestCase):
    def test_deactivates_removed_entries(self):
        """Entries present in first sync but absent in second are deactivated."""
        with patch('requests.get', return_value=_mock_response(OFAC_XML)):
            sync_list('OFAC', 'https://example.com/sdn.xml')

        self.assertTrue(
            ComplianceSanctionRecord.objects.filter(list_source='OFAC', is_active=True).exists()
        )

        # Second sync with empty list — all existing entries should be deactivated
        empty_xml = b'<sdnList></sdnList>'
        with patch('requests.get', return_value=_mock_response(empty_xml)):
            sync_list('OFAC', 'https://example.com/sdn.xml')

        self.assertFalse(
            ComplianceSanctionRecord.objects.filter(list_source='OFAC', is_active=True).exists()
        )


class ManagementCommandTest(TestCase):
    def test_command_outputs_summary_and_exits_0(self):
        """sync_sanctions command writes a summary line and exits without error."""
        with patch('requests.get', return_value=_mock_response(OFAC_XML)):
            out = StringIO()
            call_command('sync_sanctions', lists='ofac', stdout=out)
            output = out.getvalue()

        self.assertIn('Synced:', output)
        self.assertIn('OFAC', output)
        self.assertIn('Added:', output)


class BeatScheduleTest(TestCase):
    def test_sync_aml_sanction_lists_in_beat_schedule(self):
        """sync_aml_sanction_lists Celery task is registered in CELERY_BEAT_SCHEDULE."""
        from django.conf import settings
        schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
        entry = schedule.get('sync-aml-sanctions')
        self.assertIsNotNone(entry, "'sync-aml-sanctions' not found in CELERY_BEAT_SCHEDULE")
        self.assertEqual(entry['task'], 'apps.compliance.tasks.sync_aml_sanction_lists')
