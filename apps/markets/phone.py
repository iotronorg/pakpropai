from __future__ import annotations

# Longest-prefix-first dial-code map.
# Keys are digit strings (no '+'), ordered so longer prefixes shadow shorter ones.
_DIAL_CODES: dict[str, str] = {
    # 3-digit prefixes
    '971': 'AE',  # UAE
    '966': 'SA',  # Saudi Arabia
    '968': 'OM',  # Oman
    '974': 'QA',  # Qatar
    '973': 'BH',  # Bahrain
    '965': 'KW',  # Kuwait
    '961': 'LB',  # Lebanon
    '962': 'JO',  # Jordan
    '963': 'SY',  # Syria
    '964': 'IQ',  # Iraq
    '960': 'MV',  # Maldives
    '977': 'NP',  # Nepal
    '975': 'BT',  # Bhutan
    '880': 'BD',  # Bangladesh
    '852': 'HK',  # Hong Kong
    '853': 'MO',  # Macau
    '855': 'KH',  # Cambodia
    '856': 'LA',  # Laos
    '354': 'IS',  # Iceland
    '353': 'IE',  # Ireland
    '358': 'FI',  # Finland
    '351': 'PT',  # Portugal
    '420': 'CZ',  # Czech Republic
    '421': 'SK',  # Slovakia
    # 2-digit prefixes
    '92': 'PK',   # Pakistan
    '91': 'IN',   # India
    '44': 'GB',   # United Kingdom
    '61': 'AU',   # Australia
    '64': 'NZ',   # New Zealand
    '49': 'DE',   # Germany
    '33': 'FR',   # France
    '34': 'ES',   # Spain
    '39': 'IT',   # Italy
    '31': 'NL',   # Netherlands
    '32': 'BE',   # Belgium
    '46': 'SE',   # Sweden
    '47': 'NO',   # Norway
    '45': 'DK',   # Denmark
    '41': 'CH',   # Switzerland
    '43': 'AT',   # Austria
    '48': 'PL',   # Poland
    '90': 'TR',   # Turkey
    '62': 'ID',   # Indonesia
    '60': 'MY',   # Malaysia
    '65': 'SG',   # Singapore
    '66': 'TH',   # Thailand
    '84': 'VN',   # Vietnam
    '63': 'PH',   # Philippines
    '82': 'KR',   # South Korea
    '81': 'JP',   # Japan
    '86': 'CN',   # China
    '55': 'BR',   # Brazil
    '52': 'MX',   # Mexico
    '54': 'AR',   # Argentina
    '56': 'CL',   # Chile
    '57': 'CO',   # Colombia
    '27': 'ZA',   # South Africa
    '20': 'EG',   # Egypt
    '98': 'IR',   # Iran
    # 1-digit prefix (lowest priority — matches last)
    '1': 'US',    # USA / Canada / Caribbean
}

# Sorted from longest to shortest for greedy prefix matching
_SORTED_CODES = sorted(_DIAL_CODES.keys(), key=len, reverse=True)

_FALLBACK = 'PK'


class PhoneCountryResolver:
    """
    Resolves an E.164 phone number string to an ISO 3166-1 alpha-2 country code
    using longest-prefix matching against the international dial-code table.

    Examples:
        '+923001234567' → 'PK'
        '+971501234567' → 'AE'
        '+447700900123' → 'GB'
        '+12125551234'  → 'US'
    """

    @staticmethod
    def resolve(phone: str) -> str:
        """Return country code for phone, or _FALLBACK ('PK') if unrecognised."""
        if not phone:
            return _FALLBACK
        digits = phone.lstrip('+').strip()
        for code in _SORTED_CODES:
            if digits.startswith(code):
                return _DIAL_CODES[code]
        return _FALLBACK
