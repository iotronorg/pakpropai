"""UAE document type definitions — keywords and display labels."""

DOC_KEYWORDS: dict[str, str] = {
    'title deed':        'title_deed',
    'title':             'title_deed',
    'noc':               'noc',
    'no objection':      'noc',
    'oqood':             'oqood',
    'off-plan':          'oqood',
    'off plan':          'oqood',
    'emirates id':       'emirates_id',
    'eid':               'emirates_id',
    'identity':          'emirates_id',
    'passport':          'passport',
    'poa':               'poa',
    'power of attorney': 'poa',
}

DOC_TYPE_LABELS: dict[str, str] = {
    'title_deed':  'Title Deed (DLD)',
    'noc':         'No Objection Certificate',
    'oqood':       'Oqood (Off-plan Registration)',
    'emirates_id': 'Emirates ID',
    'passport':    'Passport',
    'poa':         'Power of Attorney',
    'other':       'Property Document',
}
