"""
Management command: sync AML sanction lists from OFAC, UN, and EU.

Usage:
    python manage.py sync_sanctions
    python manage.py sync_sanctions --lists ofac,un
    python manage.py sync_sanctions --lists eu
"""
import hashlib
import logging
import unicodedata
import xml.etree.ElementTree as ET

import requests
from django.core.management.base import BaseCommand

from apps.compliance.models import ComplianceSanctionRecord

logger = logging.getLogger(__name__)

SOURCES = {
    'ofac': 'https://www.treasury.gov/ofac/downloads/sdn.xml',
    'un':   'https://scsanctions.un.org/resources/xml/en/consolidated.xml',
    'eu':   'https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content',
}

FETCH_TIMEOUT = 60


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    nfkd    = unicodedata.normalize('NFKD', name or '')
    stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ' '.join(stripped.lower().split())[:200]


def _hash_id(id_number: str) -> str:
    return hashlib.sha256((id_number or '').strip().encode()).hexdigest()


def _strip_ns(root) -> None:
    """Strip XML namespace prefixes from all element tags in-place."""
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]


# ── Per-source parsers ────────────────────────────────────────────────────────

def _parse_ofac(root) -> list[tuple[str, str]]:
    """Return [(name, id_number), ...] from OFAC SDN XML."""
    _strip_ns(root)
    entries = []
    for entry in root.iter('sdnEntry'):
        last  = (entry.findtext('lastName')  or '').strip()
        first = (entry.findtext('firstName') or '').strip()
        name  = f'{first} {last}'.strip() if first else last
        id_num = ''
        id_list = entry.find('idList')
        if id_list is not None:
            first_id = id_list.find('id')
            if first_id is not None:
                id_num = (first_id.findtext('idNumber') or '').strip()
        if name:
            entries.append((name, id_num))
    return entries


def _parse_un(root) -> list[tuple[str, str]]:
    """Return [(name, id_number), ...] from UN Consolidated XML."""
    _strip_ns(root)
    entries = []
    for ind in root.iter('INDIVIDUAL'):
        parts = [
            (ind.findtext('FIRST_NAME')  or '').strip(),
            (ind.findtext('SECOND_NAME') or '').strip(),
            (ind.findtext('THIRD_NAME')  or '').strip(),
            (ind.findtext('FOURTH_NAME') or '').strip(),
        ]
        name = ' '.join(p for p in parts if p)
        id_num = ''
        doc = ind.find('INDIVIDUAL_DOCUMENT')
        if doc is not None:
            id_num = (doc.findtext('NUMBER') or '').strip()
        if name:
            entries.append((name, id_num))
    for entity in root.iter('ENTITY'):
        name = (entity.findtext('FIRST_NAME') or '').strip()
        if name:
            entries.append((name, ''))
    return entries


def _parse_eu(root) -> list[tuple[str, str]]:
    """Return [(name, id_number), ...] from EU Financial Sanctions XML."""
    _strip_ns(root)
    entries = []
    for entity in root.iter('sanctionEntity'):
        name = ''
        for alias in entity.iter('nameAlias'):
            for attr in ('fullName', 'wholeName'):
                val = (alias.get(attr) or '').strip()
                if val:
                    name = val
                    break
            if not name:
                last  = (alias.get('lastName',  '') or '').strip()
                first = (alias.get('firstName', '') or '').strip()
                name  = f'{first} {last}'.strip() if (first or last) else ''
            if name:
                break
        id_num = ''
        for ident in entity.iter('identification'):
            id_num = (ident.get('number') or '').strip()
            if id_num:
                break
        if name:
            entries.append((name, id_num))
    return entries


_PARSERS = {
    'OFAC': _parse_ofac,
    'UN':   _parse_un,
    'EU':   _parse_eu,
}


# ── Core sync logic (importable for Celery task and tests) ────────────────────

def sync_list(list_source: str, url: str) -> dict:
    """
    Download, parse, and upsert ComplianceSanctionRecord entries for one list.
    Returns {'total', 'added', 'updated', 'deactivated'}.
    Raises on network or XML errors (Celery will retry).
    """
    logger.info('sync_sanctions: fetching %s from %s', list_source, url)
    resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={'User-Agent': 'RealTron-AML-Sync/1.0'})
    resp.raise_for_status()

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise RuntimeError(f'XML parse error for {list_source}: {exc}') from exc

    raw_entries = _PARSERS[list_source](root)

    added = updated = 0
    seen_names: set[str] = set()

    for name, id_num in raw_entries:
        norm = _normalize(name)
        if not norm:
            continue
        seen_names.add(norm)

        id_prefix = (id_num or '')[:4]
        id_hash   = _hash_id(id_num) if id_num else ''

        existing = ComplianceSanctionRecord.objects.filter(
            name=norm, list_source=list_source, org=None
        ).first()

        if existing:
            changed = False
            if not existing.is_active:
                existing.is_active = True
                changed = True
            if id_hash and existing.id_number_hash != id_hash:
                existing.id_number_hash   = id_hash
                existing.id_number_prefix = id_prefix
                changed = True
            if changed:
                existing.save()
                updated += 1
        else:
            ComplianceSanctionRecord.objects.create(
                name             = norm,
                list_source      = list_source,
                org              = None,
                id_number_prefix = id_prefix,
                id_number_hash   = id_hash,
                risk_level       = ComplianceSanctionRecord.RiskLevel.HIGH,
                is_active        = True,
            )
            added += 1

    deactivated = ComplianceSanctionRecord.objects.filter(
        list_source=list_source,
        org=None,
        is_active=True,
    ).exclude(name__in=seen_names).update(is_active=False)

    total = len(seen_names)
    logger.info(
        'sync_sanctions: %s — total=%d added=%d updated=%d deactivated=%d',
        list_source, total, added, updated, deactivated,
    )
    return {'total': total, 'added': added, 'updated': updated, 'deactivated': deactivated}


# ── Management command ────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = 'Sync AML sanction lists (OFAC, UN, EU) into ComplianceSanctionRecord'

    def add_arguments(self, parser):
        parser.add_argument(
            '--lists',
            default='ofac,un,eu',
            help='Comma-separated sources to sync: ofac, un, eu',
        )

    def handle(self, *args, **options):
        requested = {s.strip().lower() for s in options['lists'].split(',')}
        source_map = {
            'ofac': ('OFAC', SOURCES['ofac']),
            'un':   ('UN',   SOURCES['un']),
            'eu':   ('EU',   SOURCES['eu']),
        }

        all_stats: dict[str, dict] = {}
        for key, (list_source, url) in source_map.items():
            if key not in requested:
                continue
            try:
                all_stats[list_source] = sync_list(list_source, url)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'ERROR syncing {list_source}: {exc}'))
                logger.error('sync_sanctions: %s failed: %s', list_source, exc, exc_info=True)

        if not all_stats:
            self.stdout.write('No lists synced.')
            return

        parts          = [f"{src} {s['total']}" for src, s in all_stats.items()]
        added_total    = sum(s['added']       for s in all_stats.values())
        updated_total  = sum(s['updated']     for s in all_stats.values())
        deact_total    = sum(s['deactivated'] for s in all_stats.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"Synced: {' | '.join(parts)} — "
                f"Added: {added_total}, Updated: {updated_total}, Deactivated: {deact_total}"
            )
        )
