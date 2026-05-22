"""UK document type definitions — keywords and display labels."""

DOC_KEYWORDS: dict[str, str] = {
    'title register':    'title_register',
    'land registry':     'title_register',
    'hm land':           'title_register',
    'land certificate':  'land_certificate',
    'mortgage deed':     'mortgage_deed',
    'mortgage':          'mortgage_deed',
    'passport':          'passport',
    'driving licence':   'driving_licence',
    'driving license':   'driving_licence',
    'poa':               'poa',
    'power of attorney': 'poa',
}

DOC_TYPE_LABELS: dict[str, str] = {
    'title_register':   'Title Register (HM Land Registry)',
    'land_certificate': 'Land Certificate',
    'mortgage_deed':    'Mortgage Deed',
    'passport':         'Passport',
    'driving_licence':  'Driving Licence',
    'poa':              'Power of Attorney',
    'other':            'Property Document',
}
