"""
Pakistan market pack (ADR-019, T-1050b).

Code that is Pakistan-specific by design (the property audit engine with its FBR
tax rules and per-marla benchmarks, the WhatsApp audit/listing tool contract,
JazzCash/EasyPaisa/Safepay) imports these constants instead of repeating the
literals, so the PK coupling is explicit and searchable.
"""
COUNTRY = 'PK'
CURRENCY = 'PKR'
AREA_UNIT = 'marla'
