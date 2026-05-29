"""
Management command: python manage.py run_eval

Runs the eval harness against a JSONL test set and writes a markdown report.
Exits with code 1 if overall_score < min_pass_score.
"""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.ml.eval_harness import EvalHarness, EvalBelowThresholdError
from apps.ml.eval_report import EvalReporter


class Command(BaseCommand):
    help = 'Run the eval harness against a JSONL test set and generate a markdown report.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dataset', required=True,
            help='Path to the eval JSONL file (each line: {"input":..., "expected_output":..., "intent":...})',
        )
        parser.add_argument(
            '--finetuned-endpoint', default=None,
            help='Optional URL of a fine-tuned model endpoint for comparison.',
        )
        parser.add_argument(
            '--min-pass-score', type=float, default=7.0,
            help='Minimum overall score to pass; exits with code 1 if below (default: 7.0)',
        )
        parser.add_argument(
            '--report-dir', default='docs/ml/eval-reports',
            help='Directory for the generated markdown report (default: docs/ml/eval-reports)',
        )

    def handle(self, *args, **options):
        dataset_path      = Path(options['dataset'])
        finetuned_endpoint = options['finetuned_endpoint']
        min_pass_score    = options['min_pass_score']
        report_dir        = Path(options['report_dir'])

        if not dataset_path.exists():
            self.stderr.write(f'Dataset not found: {dataset_path}')
            sys.exit(1)

        harness = EvalHarness(
            dataset_path=dataset_path,
            finetuned_endpoint=finetuned_endpoint,
            min_pass_score=min_pass_score,
        )
        n = harness.load()
        self.stdout.write(f'Loaded {n} eval cases from {dataset_path}')

        try:
            summary = harness.run()
            self.stdout.write(self.style.SUCCESS(
                f'\nEval complete — overall score: {summary.mean_overall:.2f} '
                f'(threshold: {min_pass_score})\n'
                f'  Accuracy: {summary.mean_accuracy:.2f}  '
                f'Tone: {summary.mean_tone:.2f}  '
                f'Intent: {summary.mean_intent:.2f}  '
                f'Infractions: {summary.total_infractions}'
            ))

            reporter = EvalReporter(output_dir=report_dir)
            report_path = reporter.generate(summary)
            self.stdout.write(f'Report written to: {report_path}')

        except EvalBelowThresholdError as exc:
            self.stderr.write(self.style.ERROR(f'\nEval FAILED: {exc}'))
            # Still write the report for debugging
            try:
                reporter = EvalReporter(output_dir=report_dir)
                reporter.generate(harness._cases and EvalHarness.__new__(EvalHarness) or None)
            except Exception:
                pass
            sys.exit(1)
