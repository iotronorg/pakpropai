"""Pakistan document type definitions — keywords and display labels."""

DOC_KEYWORDS: dict[str, str] = {
    'fard':              'fard',
    'allotment':         'allotment',
    'registry':          'sale_deed',
    'sale deed':         'sale_deed',
    'deed':              'sale_deed',
    'noc':               'noc',
    'no objection':      'noc',
    'tax certificate':   'tax_cert',
    'tax cert':          'tax_cert',
    'cvt':               'tax_cert',
    'cnic':              'cnic',
    'identity':          'cnic',
    'poa':               'poa',
    'power of attorney': 'poa',
}

DOC_TYPE_LABELS: dict[str, str] = {
    'fard':      'Fard (Ownership Record)',
    'allotment': 'Allotment Letter',
    'sale_deed': 'Sale Deed / Registry',
    'noc':       'No Objection Certificate',
    'tax_cert':  'Tax Certificate',
    'cnic':      'CNIC',
    'poa':       'Power of Attorney',
    'other':     'Property Document',
}
