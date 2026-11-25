"""
AI eval gate (T-2070). Exit code 1 when a gate fails.

    manage.py run_ai_evals                       # deterministic suites (CI)
    manage.py run_ai_evals --live                # plus the live suite: calls a model, costs money
    manage.py run_ai_evals --live --write-baseline
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai.backends import PROVIDERS, make_backend
from apps.ai.evals import live, offline


class Command(BaseCommand):
    help = 'Run the AI eval suites and fail when a gate is not met.'

    def add_arguments(self, parser):
        parser.add_argument('--suite', action='append', choices=list(offline.SUITES), help='Default: all')
        parser.add_argument('--live', action='store_true', help='Also call a real model (costs money)')
        parser.add_argument('--provider', choices=PROVIDERS, default='gemini')
        parser.add_argument('--baseline', default=str(live.BASELINE))
        parser.add_argument('--write-baseline', action='store_true',
                            help='Accept this live run as the new baseline (refused when the run is incomplete)')
        parser.add_argument('--report', help='Write the results as JSON')

    def handle(self, *args, **o):
        if o['write_baseline'] and not o['live']:
            raise CommandError('--write-baseline needs --live.')
        report = {'at': timezone.now().isoformat(timespec='seconds'), 'suites': [], 'live': None}
        problems = []
        for result in offline.run(o['suite']):
            report['suites'].append(result.as_dict())
            self.stdout.write(f'{result.name}: {result.total - len(result.failures)}/{result.total}')
            for f in result.failures:
                self.stdout.write(f'  FAIL {f["id"]} expected {f["expected"]} got {f["got"]}')
            if not result.ok:
                problems.append(f'{result.name}: {len(result.failures)} of {result.total} failed')
        baseline_path = Path(o['baseline'])
        baseline = live.read_baseline(baseline_path)
        if o['live']:
            problems += self._live(o, report, baseline, baseline_path)
        elif baseline and baseline.get('fingerprint') != live.fingerprint():
            self.stdout.write(self.style.WARNING(
                'Prompts, tools or cases changed since the live baseline: run --live before release.'))
        if o['report']:
            Path(o['report']).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        if problems:
            raise CommandError('Eval gate failed:\n- ' + '\n- '.join(problems))
        self.stdout.write(self.style.SUCCESS('Eval gate passed.'))

    def _live(self, o, report, baseline, path) -> list[str]:
        backend = make_backend(o['provider'])
        if not backend.is_configured():
            raise CommandError(f'Provider {o["provider"]} is not configured.')
        report['live'] = result = live.run(backend)
        for lang, s in result['languages'].items():
            self.stdout.write(f'live {lang}: {s}')
        if result['below_target_size']:
            self.stdout.write(self.style.WARNING(
                f'Fewer than {live.TARGET_PER_LANGUAGE} cases: {", ".join(result["below_target_size"])}'))
        problems = live.gate(result['languages'], None if o['write_baseline'] else baseline)
        if o['write_baseline']:
            problems = [p for p in problems if 'no baseline' not in p]
            if not problems:
                keep = {k: result[k] for k in ('provider', 'model', 'fingerprint', 'languages')}
                path.write_text(json.dumps({'recorded_at': report['at'], **keep}, indent=2) + '\n', encoding='utf-8')
        return problems
