"""
Report how listings and organisations map to markets (T-1050a, ADR-019).

    python manage.py market_backfill_report                 # counts
    python manage.py market_backfill_report --apply         # fill NULL markets from country
    python manage.py market_backfill_report --max-unmatched-pct 1   # exit 1 above 1 %
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.db.models.functions import Upper

from apps.markets.models import Market
from apps.organizations.models import Organization
from apps.properties.models import Property


class Command(BaseCommand):
    help = 'Per-market counts of listings/organisations and rows without a market.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Set NULL markets from country.')
        parser.add_argument('--max-unmatched-pct', type=float, default=None,
                            help='Exit with an error if more listings than this %% have no market.')

    def handle(self, *args, apply=False, max_unmatched_pct=None, **options):
        codes = list(Market.objects.values_list('code', flat=True))
        worst = 0.0
        for model, label in ((Property, 'listings'), (Organization, 'organizations')):
            if apply:
                fixed = 0
                for code in codes:
                    fixed += (model.objects.annotate(cc=Upper('country'))
                              .filter(market__isnull=True, cc=code).update(market_id=code))
                self.stdout.write(f'{label}: filled {fixed}')
            total = model.objects.count()
            per_market = dict(model.objects.exclude(market__isnull=True)
                              .values_list('market_id').annotate(n=Count('pk')))
            unmatched = model.objects.filter(market__isnull=True)
            n_unmatched = unmatched.count()
            pct = (100.0 * n_unmatched / total) if total else 0.0
            if model is Property:
                worst = pct
            self.stdout.write(f'{label}: total={total} unmatched={n_unmatched} ({pct:.2f}%)')
            for code in sorted(per_market):
                self.stdout.write(f'  {code}: {per_market[code]}')
            top = (unmatched.annotate(cc=Upper('country')).values_list('cc')
                   .annotate(n=Count('pk')).order_by('-n')[:10])
            for cc, n in top:
                self.stdout.write(f'  unmatched country {cc or "(blank)"}: {n}')
        if max_unmatched_pct is not None and worst > max_unmatched_pct:
            raise CommandError(f'{worst:.2f}% of listings have no market (limit {max_unmatched_pct}%).')
