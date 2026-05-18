"""Document OCR helpers — detect type, build prompt, parse and format response."""

_DOC_KEYWORDS = {
    'fard':             'fard',
    'allotment':        'allotment',
    'registry':         'sale_deed',
    'sale deed':        'sale_deed',
    'deed':             'sale_deed',
    'noc':              'noc',
    'no objection':     'noc',
    'tax certificate':  'tax_cert',
    'tax cert':         'tax_cert',
    'cvt':              'tax_cert',
    'cnic':             'cnic',
    'identity':         'cnic',
    'poa':              'poa',
    'power of attorney':'poa',
}


def detect_doc_type(caption: str) -> str:
    cap = caption.lower()
    for keyword, doc_type in _DOC_KEYWORDS.items():
        if keyword in cap:
            return doc_type
    return 'other'


def ocr_prompt(doc_type: str, caption: str) -> str:
    type_guidance = {
        'fard': (
            "This is a Fard (ownership record) from PLRA or revenue department. "
            "Extract: owner name, CNIC number, Khasra/Khatuni number, property address, "
            "area (ruqba in marla/kanal), registration date, issuing authority."
        ),
        'allotment': (
            "This is an Allotment Letter from a housing authority. "
            "Extract: allottee name, CNIC, plot/house number, scheme/society name, "
            "area, allotment date, authority name, ballot number if visible."
        ),
        'sale_deed': (
            "This is a Sale Deed or Registry document. "
            "Extract: buyer name, seller name, buyer CNIC, seller CNIC, "
            "property address, area, sale price (PKR), registration date, "
            "Sub-Registrar office, stamp duty paid."
        ),
        'noc': (
            "This is a No Objection Certificate (NOC). "
            "Extract: applicant name, property address, issuing authority (LDA/DHA/CDA), "
            "NOC number, issue date, expiry date if any, purpose of NOC."
        ),
        'tax_cert': (
            "This is a property tax certificate. "
            "Extract: property owner name, property address, tax amount, "
            "tax year, payment date, FBR or local body reference number."
        ),
        'cnic': (
            "This is a Pakistani CNIC card. "
            "Extract: full name, CNIC number (format: XXXXX-XXXXXXX-X), "
            "date of birth, issue date, expiry date, address."
        ),
        'poa': (
            "This is a Power of Attorney document. "
            "Extract: principal name, principal CNIC, attorney name, attorney CNIC, "
            "scope of authority, property details if mentioned, "
            "notary registration number, date, expiry if any."
        ),
    }.get(doc_type, "This is a property-related document.")

    return (
        f"{type_guidance}\n\n"
        "Also check for these red flags:\n"
        "- Overwriting, cutting, or corrections on important fields\n"
        "- Blurred or missing official stamps/signatures\n"
        "- Mismatch between names and CNIC numbers\n"
        "- Photocopied or digitally altered stamps\n"
        "- Missing registration numbers\n\n"
        "Return your response in this exact format:\n"
        "OWNER: [name or N/A]\n"
        "CNIC: [number or N/A]\n"
        "ADDRESS: [property address or N/A]\n"
        "AREA: [area in marla/kanal or N/A]\n"
        "REG_NUMBER: [registration/reference number or N/A]\n"
        "DATE: [issue/registration date or N/A]\n"
        "AUTHORITY: [issuing body or N/A]\n"
        "FLAGS: [comma-separated red flags, or NONE]\n"
        "CONFIDENCE: [HIGH / MEDIUM / LOW — based on image clarity]\n"
        "NOTES: [any additional important observations]\n\n"
        f"User caption: '{caption}'"
    )


def parse_ocr_response(raw: str, doc_type: str) -> dict:
    result = {
        'owner_name': '', 'cnic': '', 'address': '', 'area': '',
        'registration_number': '', 'date': '', 'authority': '',
        'flags': [], 'confidence': 'LOW', 'notes': '',
    }
    field_map = {
        'OWNER':      'owner_name',
        'CNIC':       'cnic',
        'ADDRESS':    'address',
        'AREA':       'area',
        'REG_NUMBER': 'registration_number',
        'DATE':       'date',
        'AUTHORITY':  'authority',
        'CONFIDENCE': 'confidence',
        'NOTES':      'notes',
    }
    for line in raw.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().upper()
        val = val.strip()
        if not val or val == 'N/A':
            continue
        if key == 'FLAGS':
            result['flags'] = [f.strip() for f in val.split(',') if f.strip().lower() != 'none']
        elif key in field_map:
            result[field_map[key]] = val
    return result


def format_doc_summary(result: dict, doc_type: str) -> str:
    doc_labels = {
        'fard': 'Fard (Ownership Record)',
        'allotment': 'Allotment Letter',
        'sale_deed': 'Sale Deed / Registry',
        'noc': 'NOC',
        'tax_cert': 'Tax Certificate',
        'cnic': 'CNIC',
        'poa': 'Power of Attorney',
        'other': 'Property Document',
    }
    label      = doc_labels.get(doc_type, 'Document')
    flags      = result.get('flags', [])
    confidence = result.get('confidence', 'LOW')
    status_icon = '✅' if not flags else '⚠️'

    lines = [
        f"📄 *DOCUMENT SCAN — {label}*",
        f"Confidence: {confidence} | Status: {'CLEAN' if not flags else 'SUSPICIOUS'} {status_icon}",
        "",
    ]

    field_labels = {
        'owner_name':          '👤 Owner',
        'cnic':                '🪪 CNIC',
        'address':             '📍 Address',
        'area':                '📐 Area',
        'registration_number': '🔢 Ref/Reg No',
        'date':                '📅 Date',
        'authority':           '🏛 Authority',
    }
    for field, lbl in field_labels.items():
        val = result.get(field, '').strip()
        if val:
            lines.append(f"{lbl}: {val}")

    if result.get('notes'):
        lines += ['', f"📝 Notes: {result['notes']}"]

    if flags:
        lines += ['', '⚠️ *RED FLAGS DETECTED:*']
        for f in flags:
            lines.append(f"• {f}")
        lines += [
            '',
            '*Recommendation:* Do NOT proceed with this document until all flags are resolved.',
            'Consult a property lawyer or visit the issuing authority to verify.'
        ]
    else:
        lines += [
            '',
            '✅ No obvious red flags detected in this document.',
            '_Always verify originals at the issuing authority before any transaction._',
        ]

    return '\n'.join(lines)
