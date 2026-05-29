"""
Management command: python manage.py curate_dataset

Scans WhatsApp sessions with HANDOVER or DEAL_LOCK outcomes, scrubs PII,
and writes an instruction-tuning JSONL dataset.
"""
import os
from pathlib import Path
from datetime import date

from django.core.management.base import BaseCommand

from apps.ml.dataset_curator import ConversationScanner
from apps.ml.jsonl_builder import InstructionTuningBuilder


class Command(BaseCommand):
    help = 'Curate an instruction-tuning dataset from WhatsApp conversation history.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default='datasets/',
            help='Output directory for the JSONL file (default: datasets/)',
        )
        parser.add_argument(
            '--min-quality', type=float, default=0.7,
            help='Minimum quality score threshold 0.0–1.0 (default: 0.7)',
        )
        parser.add_argument(
            '--limit', type=int, default=5000,
            help='Maximum number of candidate sessions to scan (default: 5000)',
        )

    def handle(self, *args, **options):
        output_dir  = Path(options['output'])
        min_quality = options['min_quality']
        limit       = options['limit']

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f'realtron-{date.today().isoformat()}.jsonl'

        self.stdout.write(
            f'Scanning conversations (min_quality={min_quality}, limit={limit})…'
        )

        scanner = ConversationScanner(min_quality=min_quality, limit=limit)
        candidates = scanner.scan()
        self.stdout.write(f'Found {len(candidates)} candidate conversations.')

        builder = InstructionTuningBuilder()
        stats = builder.build_dataset(candidates, output_path, min_quality=min_quality)

        self.stdout.write(self.style.SUCCESS(
            f'\nDataset written to: {output_path}\n'
            f'  Total candidates:       {stats.total_candidates}\n'
            f'  Included (scrubbed):    {stats.scrubbed_and_included}\n'
            f'  Dropped (PII leak):     {stats.dropped_pii_leak}\n'
            f'  Dropped (cross-tenant): {stats.dropped_cross_tenant}\n'
            f'  Dropped (quality):      {stats.dropped_quality_below_threshold}\n'
            f'  Dropped (empty):        {stats.dropped_empty}'
        ))
