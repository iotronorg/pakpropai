import logging

logger = logging.getLogger(__name__)


def run_fraud_check(description: str) -> dict:
    """
    Analyze a property deal, agent, or transaction for fraud and scam indicators
    based on known Pakistani real estate scam patterns.
    Call this when user says 'check fraud', 'verify this agent', 'is this legit', 'scam check',
    or describes a suspicious deal or property situation.

    Args:
        description: Full description of the property deal, agent details, or suspicious situation
    """
    try:
        desc = description.lower()
        flags = []
        score = 0

        patterns = [
            ('advance payment',     'Advance payment demanded before documents shown — HIGH RISK',       45),
            ('token first',         'Token money demanded before any documents — Major red flag',         35),
            ('overseas',            'Overseas seller — NEVER send money without verified local presence',  25),
            ('urgent sale',         'Urgency pressure tactic — common manipulation technique',            15),
            ('kachhi file',         'Kachhi (unallocated) file — verify allocation with authority',        55),
            ('kachi file',          'Kachhi (unallocated) file — verify allocation with authority',        55),
            ('file not allotted',   'File not yet allotted — speculative, high risk',                     50),
            ('power of attorney',   'PoA involved — verify it is valid, registered, and not expired',     25),
            ('court case',          'Court litigation mentioned — DO NOT buy until resolved',              65),
            ('no fard',             'Seller unable to provide Fard — serious red flag',                    55),
            ('no documents',        'Seller has no documents — extremely high risk',                       70),
            ('below market',        'Price significantly below market — possible fraud or legal issue',    25),
            ('double sale',         'Possible double sale scenario',                                       60),
            ('no noc',              'No NOC from authority — registration cannot complete',                40),
            ('society not approved','Non-approved society — no LDA/CDA NOC',                              50),
            ('fake registry',       'Fake or forged registry document — verify at sub-registrar',         70),
            ('transfer fee waived', 'Transfer fee waiver claim — verify with authority directly',         30),
            ('deal expire',         'Artificial deadline — pressure tactic to rush payment',              20),
            ('limited time',        'Artificial deadline — pressure tactic to rush payment',              20),
            ('guaranteed return',   'Guaranteed return promise — no property investment is guaranteed',   35),
            ('pehle paise',         'Advance payment demanded before documents — HIGH RISK',              45),
            ('agay payment',        'Advance payment demanded before documents — HIGH RISK',              45),
            ('pehle token',         'Token money demanded before documents — Major red flag',             35),
            ('baher se',            'Overseas seller — NEVER send money without in-person verification',  25),
            ('bahir se',            'Overseas seller — NEVER send money without in-person verification',  25),
            ('jaldi karo',          'Urgency pressure — do not rush any property decision',               20),
            ('jaldi sale',          'Urgency pressure — do not rush any property decision',               20),
            ('kachha file',         'Kachhi (unallocated) file — verify allocation at authority office',  55),
            ('poa hai',             'PoA involved — verify it is valid, registered, and not expired',     25),
            ('fard nahi',           'Seller cannot provide Fard — serious red flag',                      55),
            ('documents nahi',      'No documents available — extremely high risk',                       70),
            ('sasta hai',           'Price below market — verify reason before any payment',              20),
            ('court mein hai',      'Property in court litigation — DO NOT proceed',                      65),
            ('noc nahi',            'No NOC from authority — transfer cannot complete',                   40),
            ('double bech',         'Possible double sale — get fresh Fard before any payment',           60),
            ('already sold',        'Possible double sale — get fresh Fard before any payment',           60),
        ]

        for keyword, flag_msg, risk_pts in patterns:
            if keyword in desc:
                if flag_msg not in flags:
                    flags.append(flag_msg)
                    score += risk_pts

        score = min(score, 100)
        risk = 'high' if score >= 50 else 'medium' if score >= 25 else 'low'

        verify_steps = [
            'Get Fard (ownership record) directly from PLRA, CDA, or local land records office',
            "Verify seller's CNIC matches all property documents",
            'Check for court orders at local civil courts (free record check)',
            'Physically visit the property and confirm boundaries with a witness',
            'For DHA/Bahria: Verify file/plot at the official authority office in person',
            'For any PoA: Verify at Sub-Registrar office that it is valid and not cancelled',
        ]

        recommendation = {
            'high': 'HIGH RISK — Strong fraud indicators detected. Consult a property lawyer BEFORE any payment. Do NOT transfer any money.',
            'medium': 'MEDIUM RISK — Proceed with caution. Verify all documents thoroughly before any payment.',
            'low': 'LOW RISK — Standard verification recommended. Always complete due diligence before finalizing.',
        }[risk]

        return {
            'risk': risk,
            'risk_score': score,
            'flags': flags,
            'recommendation': recommendation,
            'verify_steps': verify_steps[:4],
        }
    except Exception as exc:
        logger.error(f"run_fraud_check tool failed: {exc}")
        return {'risk': 'unknown', 'flags': [], 'error': str(exc)}
