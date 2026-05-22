"""Document OCR helpers — detect type, build prompt, parse and format response."""


def detect_doc_type(caption: str, org_country: str = 'PK') -> str:
    from apps.markets.registry import get_doc_keywords
    cap = caption.lower()
    for keyword, doc_type in get_doc_keywords(org_country).items():
        if keyword in cap:
            return doc_type
    return 'other'


# ── OCR type guidance ─────────────────────────────────────────────────────────

_PK_GUIDANCE: dict[str, str] = {
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
        "property address, area, sale price, registration date, "
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
}

_AE_GUIDANCE: dict[str, str] = {
    'title_deed': (
        "This is a UAE Title Deed (DLD). "
        "Extract: owner name, passport/Emirates ID, property address, area (sqft), "
        "transaction value (AED), DLD registration number, issue date, "
        "mortgage status if indicated."
    ),
    'noc': (
        "This is a No Objection Certificate (NOC) from a UAE developer or authority. "
        "Extract: applicant name, property details, issuing authority, "
        "NOC number, issue date, expiry date if any."
    ),
    'oqood': (
        "This is an Oqood off-plan registration contract. "
        "Extract: buyer name, developer name, project name, unit details, "
        "agreed price (AED), payment plan, expected completion date."
    ),
    'emirates_id': (
        "This is a UAE Emirates ID card. "
        "Extract: full name, Emirates ID number (format: 784-XXXX-XXXXXXX-X), "
        "nationality, date of birth, expiry date."
    ),
    'passport': (
        "This is a passport. "
        "Extract: full name, passport number, nationality, date of birth, "
        "issue date, expiry date, issuing country."
    ),
    'poa': (
        "This is a Power of Attorney document. "
        "Extract: principal name, ID number, attorney name, attorney ID, "
        "scope of authority, property details if mentioned, "
        "notary registration number, date, expiry if any."
    ),
}

_GB_GUIDANCE: dict[str, str] = {
    'title_register': (
        "This is a UK HM Land Registry Title Register. "
        "Extract: registered owner name, title number, property address, tenure "
        "(freehold/leasehold), charges/mortgages registered, last sale price and date."
    ),
    'land_certificate': (
        "This is a UK Land Certificate. "
        "Extract: title number, owner name, property address, tenure, "
        "register entries, issue date."
    ),
    'mortgage_deed': (
        "This is a UK Mortgage Deed. "
        "Extract: borrower name, lender name, property address, "
        "mortgage amount, interest rate if visible, execution date."
    ),
    'passport': (
        "This is a passport. "
        "Extract: full name, passport number, nationality, date of birth, "
        "issue date, expiry date, issuing country."
    ),
    'driving_licence': (
        "This is a UK Driving Licence. "
        "Extract: full name, licence number, date of birth, address, "
        "issue date, expiry date, categories held."
    ),
    'poa': (
        "This is a Power of Attorney document. "
        "Extract: principal name, ID number, attorney name, attorney ID, "
        "scope of authority, property details if mentioned, "
        "notary/solicitor registration number, date, expiry if any."
    ),
}

_GUIDANCE_BY_COUNTRY: dict[str, dict[str, str]] = {
    'PK': _PK_GUIDANCE,
    'AE': _AE_GUIDANCE,
    'GB': _GB_GUIDANCE,
}


def ocr_prompt(doc_type: str, caption: str, org_country: str = 'PK') -> str:
    guidance_map = _GUIDANCE_BY_COUNTRY.get(org_country.upper(), _PK_GUIDANCE)
    type_guidance = guidance_map.get(doc_type, "This is a property-related document.")

    return (
        f"{type_guidance}\n\n"
        "Also check for these red flags:\n"
        "- Overwriting, cutting, or corrections on important fields\n"
        "- Blurred or missing official stamps/signatures\n"
        "- Mismatch between names and ID numbers\n"
        "- Photocopied or digitally altered stamps\n"
        "- Missing registration numbers\n\n"
        "Return your response in this exact format:\n"
        "OWNER: [name or N/A]\n"
        "ID_NUMBER: [national ID / CNIC / Emirates ID / passport number or N/A]\n"
        "ADDRESS: [property address or N/A]\n"
        "AREA: [area with unit or N/A]\n"
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
        'CNIC':       'cnic',       # backward compat — old responses
        'ID_NUMBER':  'cnic',       # new unified field name
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


def format_doc_summary(result: dict, doc_type: str, org_country: str = 'PK') -> str:
    from apps.markets.registry import get_doc_type_labels
    labels   = get_doc_type_labels(org_country)
    label    = labels.get(doc_type, 'Document')
    flags    = result.get('flags', [])
    confidence = result.get('confidence', 'LOW')
    status_icon = '✅' if not flags else '⚠️'

    lines = [
        f"📄 *DOCUMENT SCAN — {label}*",
        f"Confidence: {confidence} | Status: {'CLEAN' if not flags else 'SUSPICIOUS'} {status_icon}",
        "",
    ]

    field_labels = {
        'owner_name':          '👤 Owner',
        'cnic':                '🪪 ID Number',
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
