"""
Seed the location hierarchy (T-1060a): a country node per market, PK
provinces/territories and the major cities (the ref-number city map), a few
well-known Lahore/Karachi/Islamabad localities, and the AE/GB top levels.
Idempotent (get_or_create on path). Values are frozen here; the normaliser is
a frozen copy of apps.locations.services.normalise (a test keeps them equal).
Reverse deletes seeded rows that have no non-seed children.
"""
import re
import unicodedata

from django.db import migrations

_SYN = {'ph': 'phase', 'phs': 'phase', 'blk': 'block', 'bl': 'block', 'sec': 'sector',
        'sect': 'sector', 'st': 'street', 'rd': 'road', 'ext': 'extension', 'twn': 'town',
        'soc': 'society', 'no': '', 'number': '', 'nr': ''}


def _norm(text):
    out, letters = [], []
    for tok in re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text[:150]).lower()):
        tok = _SYN.get(tok, tok)
        if tok.isdigit():
            tok = str(int(tok))
        if len(tok) == 1 and tok.isalpha():
            letters.append(tok)
            continue
        if letters:
            out.append(''.join(letters))
            letters = []
        if tok:
            out.append(tok)
    if letters:
        out.append(''.join(letters))
    return ' '.join(out)


def _slug(name):
    from django.utils.text import slugify
    return slugify(name, allow_unicode=True)[:160]


COUNTRIES = {'PK': 'Pakistan', 'AE': 'United Arab Emirates', 'GB': 'United Kingdom',
             'US': 'United States', 'CA': 'Canada'}

# (market, region, [(city, aliases, [(locality, aliases)])])
TREE = [
    ('PK', 'Punjab', [
        ('Lahore', ['LHR'], [('DHA', ['Defence', 'Defence Housing Authority']), ('Bahria Town', []),
                             ('Gulberg', []), ('Johar Town', []), ('Model Town', []), ('Wapda Town', []),
                             ('Askari', [])]),
        ('Rawalpindi', ['Pindi', 'RWP'], [('Bahria Town', []), ('DHA', ['Defence'])]),
        ('Faisalabad', ['Lyallpur'], []), ('Multan', [], [('DHA', ['Defence'])]),
        ('Gujranwala', [], []), ('Sialkot', [], []), ('Bahawalpur', [], []), ('Sargodha', [], []),
        ('Sheikhupura', [], []), ('Rahim Yar Khan', ['RYK'], []), ('Gujrat', [], []),
        ('Sahiwal', [], []), ('Dera Ghazi Khan', ['DG Khan'], []), ('Wah Cantt', ['Wah'], []),
        ('Chiniot', [], []),
    ]),
    ('PK', 'Sindh', [
        ('Karachi', ['KHI'], [('DHA', ['Defence', 'Defence Housing Authority']), ('Clifton', []),
                              ('Gulshan-e-Iqbal', ['Gulshan e Iqbal', 'Gulshan']), ('Bahria Town', []),
                              ('North Nazimabad', []), ('PECHS', [])]),
        ('Hyderabad', [], []), ('Sukkur', [], []), ('Larkana', [], []),
    ]),
    ('PK', 'Khyber Pakhtunkhwa', [('Peshawar', [], [('Hayatabad', [])]), ('Abbottabad', [], []),
                                  ('Mardan', [], [])]),
    ('PK', 'Balochistan', [('Quetta', [], [])]),
    ('PK', 'Islamabad Capital Territory', [
        ('Islamabad', ['ISB'], [('DHA', ['Defence']), ('Bahria Town', []), ('F-7', []), ('F-10', []),
                                ('E-11', []), ('G-11', []), ('B-17', [])]),
    ]),
    ('PK', 'Gilgit-Baltistan', []),
    ('PK', 'Azad Jammu and Kashmir', [('Muzaffarabad', [], []), ('Mirpur', [], [])]),
    ('AE', 'Dubai', [('Dubai', [], [('Dubai Marina', ['Marina']), ('Downtown Dubai', ['Downtown']),
                                   ('Jumeirah Village Circle', ['JVC']), ('Business Bay', [])])]),
    ('AE', 'Abu Dhabi', [('Abu Dhabi', [], [])]),
    ('AE', 'Sharjah', [('Sharjah', [], [])]),
    ('GB', 'England', [('London', [], []), ('Manchester', [], []), ('Birmingham', [], [])]),
    ('GB', 'Scotland', [('Edinburgh', [], []), ('Glasgow', [], [])]),
    ('GB', 'Wales', [('Cardiff', [], [])]),
    ('GB', 'Northern Ireland', [('Belfast', [], [])]),
]


def _node(Location, market, parent, level, name, aliases=()):
    slug = market.lower() if parent is None else _slug(name)
    path = slug if parent is None else f'{parent.path}/{slug}'
    obj, _ = Location.objects.get_or_create(path=path, defaults=dict(
        market_id=market, parent=parent, level=level, name=name, slug=slug,
        match_key=_norm(name), aliases=list(aliases), is_verified=True, source='seed'))
    return obj


def seed(apps, schema_editor):
    Location = apps.get_model('locations', 'Location')
    Market = apps.get_model('markets', 'Market')
    present = set(Market.objects.values_list('code', flat=True))
    roots = {code: _node(Location, code, None, 'country', name)
             for code, name in COUNTRIES.items() if code in present}
    for market, region, cities in TREE:
        if market not in roots:
            continue
        r = _node(Location, market, roots[market], 'region', region)
        for city, c_aliases, localities in cities:
            c = _node(Location, market, r, 'city', city, c_aliases)
            for loc, l_aliases in localities:
                _node(Location, market, c, 'locality', loc, l_aliases)


def unseed(apps, schema_editor):
    # properties 0038 (location_ref) depends on this migration, so it is already
    # unapplied here: no listing can reference a location.
    Location = apps.get_model('locations', 'Location')
    for level in ('locality', 'city', 'region', 'country'):       # deepest first (PROTECT)
        for loc in Location.objects.filter(source='seed', level=level):
            if not Location.objects.filter(parent=loc).exists():
                loc.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('locations', '0001_location'),
        ('markets', '0004_seed_listing_schemas'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
