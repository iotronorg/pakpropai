"""
Index-backed full-text search over listings (T-1100, ADR-015).

The GIN index ``prop_fts_gin`` (see ``Property.Meta.indexes``) is built on the
exact expression returned by :func:`search_vector`. Postgres only uses an
expression index when the query repeats the same expression, so every
full-text filter on ``Property`` must go through :func:`apply_text_search`.
Changing ``SEARCH_CONFIG`` or ``SEARCH_FIELDS`` needs a migration that
rebuilds the index.

Why the ``simple`` configuration: an expression index needs an explicit
(immutable) text-search config, and listings are multilingual place names
(DHA, Bahria, Gulberg, Urdu/Arabic text) that English stemming mangles. Prefix
matching (``apartment`` → ``apartments``) recovers the useful part of
stemming.
"""
import re

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

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
    """The indexed document expression. Must match ``prop_fts_gin``."""
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
    order them by relevance, newest first on ties. Uses the GIN index.
    """
    stripped = (term or '').strip()
    if _REF_NO_RE.match(stripped):
        return qs.filter(ref_no=stripped.upper())
    raw = build_prefix_tsquery(term)
    if raw is None:
        return qs.none()
    query = SearchQuery(raw, search_type='raw', config=SEARCH_CONFIG)
    return (
        qs.alias(search_document=search_vector())
          .filter(search_document=query)
          .annotate(rank=SearchRank(search_vector(), query))
          .order_by('-rank', '-created_at')
    )
