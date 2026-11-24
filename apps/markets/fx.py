"""Approximate currency conversion from stored daily rates (T-2041, E2). Never used to store or charge money."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

BASE = 'USD'


@dataclass(frozen=True)
class Rates:
    """Latest usable rate per currency; older than ``FX_MAX_AGE_DAYS`` is not used."""
    per_usd: dict
    as_of: dict
    sources: dict

    def convert(self, amount, src: str, dst: str) -> int | None:
        if amount is None:
            return None
        if src == dst:
            return int(Decimal(amount).to_integral_value(ROUND_HALF_UP))
        a, b = self.per_usd.get(src), self.per_usd.get(dst)
        if not a or not b:
            return None
        return int((Decimal(amount) * b / a).to_integral_value(ROUND_HALF_UP))

    def describe(self, codes) -> dict | None:
        """What the conversion between these currencies is based on (the oldest date counts)."""
        used = sorted(c for c in codes if c in self.as_of)
        if not used:
            return None
        return {'approximate': True, 'as_of': min(self.as_of[c] for c in used),
                'source': ', '.join(sorted({self.sources[c] for c in used}))[:200]}


def rates(codes) -> Rates:
    from .models import FxRate
    today: date = timezone.now().date()
    per_usd, as_of, sources = {BASE: Decimal(1)}, {}, {}
    for r in (FxRate.objects.filter(currency__in=set(codes) - {BASE}, as_of__lte=today,
                                    as_of__gte=today - timedelta(days=settings.FX_MAX_AGE_DAYS))
              .order_by('currency', '-as_of').distinct('currency')):
        per_usd[r.currency], as_of[r.currency], sources[r.currency] = r.per_usd, r.as_of, r.source
    return Rates(per_usd, as_of, sources)
