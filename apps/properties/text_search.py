"""
Index-backed full-text search over listings (T-1100, ADR-015; T-1070f).

Queries use the stored ``search_tsv`` column (GIN ``prop_search_tsv_gin``),
which a DB trigger (migration 0035) keeps equal to :func:`search_vector`.
Ranking reads the stored vector instead of recomputing ``to_tsvector`` per
match (KI-035). Changing ``SEARCH_CONFIG`` or ``SEARCH_FIELDS`` needs a
migration that replaces the trigger function and re-runs the backfill.

:func:`search_vector` also defines the legacy expression index
``prop_fts_gin``, still used by pre-T-1070f pods until it is dropped (T-1070g).

Why the ``simple`` configuration: an expression index needs an explicit
(immutable) text-search config, and listings are multilingual place names
(DHA, Bahria, Gulberg, Urdu/Arabic text) that English stemming mangles. Prefix
matching (``apartment`` → ``apartments``) recovers the useful part of
stemming.
"""
import re

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import F

SEARCH_CONFIG = 'simple'
SEARCH_FIELDS = ('title', 'city', 'location', 'description', 'ref_no')

MAX_TERM_LENGTH = 200   # characters of user input considered
MAX_TOKENS = 8          # AND-ed terms per query

# Letters and digits only. This drops every tsquery operator (& | ! ( ) : * < >)
# and quote, so user input can never produce a malformed or widened tsquery.
_TOKEN_RE = re.compile(r'[^\W_]+', re.UNICODE)

# Generated reference numbers, e.g. RT-LHR-2026-000000001. The default text
# parser reads the trailing "-000000001" as a signed integer, so tokens cannot
# match it; these go to the unique B-tree on ref_no instead.
_REF_NO_RE = re.compile(r'^[A-Za-z]{2,5}-[A-Za-z0-9]{2,5}-\d{4}-\d{6,}$')


def search_vector() -> SearchVector:
    """The document expression: ``prop_fts_gin`` and the 0035 trigger must match it."""
    return SearchVector(*SEARCH_FIELDS, config=SEARCH_CONFIG)


def build_prefix_tsquery(term: str) -> str | None:
    """
    Turn free text into a ``to_tsquery`` string: every token must match, and
    tokens of two or more characters match as prefixes. ``None`` when the input
    has no searchable token.
    """
    tokens = _TOKEN_RE.findall((term or '')[:MAX_TERM_LENGTH].lower())[:MAX_TOKENS]
    if not tokens:
        return None
    return ' & '.join(f'{t}:*' if len(t) > 1 else t for t in tokens)


def apply_text_search(qs, term: str):
    """
    Filter ``qs`` (a ``Property`` queryset) to listings matching ``term`` and
    order them by relevance, newest first on ties. Uses ``prop_search_tsv_gin``.
    """
    stripped = (term or '').strip()
    if _REF_NO_RE.match(stripped):
        return qs.filter(ref_no=stripped.upper())
    raw = build_prefix_tsquery(term)
    if raw is None:
        return qs.none()
    query = SearchQuery(raw, search_type='raw', config=SEARCH_CONFIG)
    return (
        qs.filter(search_tsv=query)
          .annotate(rank=SearchRank(F('search_tsv'), query))
          .order_by('-rank', '-created_at')
    )
