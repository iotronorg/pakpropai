"""US document type definitions — keywords and display labels."""

DOC_KEYWORDS: dict[str, str] = {
    'warranty deed':    'warranty_deed',
    'deed':             'warranty_deed',
    'title insurance':  'title_insurance',
    'title policy':     'title_insurance',
    'hoa':              'hoa_docs',
    'homeowners association': 'hoa_docs',
    'closing disclosure': 'closing_disclosure',
    'hud-1':            'closing_disclosure',
    'promissory note':  'promissory_note',
    'mortgage note':    'promissory_note',
    'drivers license':  'drivers_license',
    "driver's license": 'drivers_license',
    'state id':         'state_id',
    'passport':         'passport',
    'poa':              'poa',
    'power of attorney': 'poa',
}

DOC_TYPE_LABELS: dict[str, str] = {
    'warranty_deed':      'Warranty Deed',
    'title_insurance':    'Title Insurance Policy',
    'hoa_docs':           'HOA Documents',
    'closing_disclosure': 'Closing Disclosure (HUD-1)',
    'promissory_note':    'Promissory Note / Mortgage Note',
    'drivers_license':    "Driver's License",
    'state_id':           'State ID',
    'passport':           'Passport',
    'poa':                'Power of Attorney',
    'other':              'Property Document',
}
