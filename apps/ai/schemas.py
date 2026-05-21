"""
Pydantic schemas for structured intent extraction and AI tool inputs.

These serve three purposes:
  1. Validated, type-safe input for direct Python tool calls (bypassing the LLM).
  2. Normalised parameters that the LLM receives in structured form (avoids hallucinating units).
  3. Documented API contracts for future REST endpoints over the AI layer.

Unit conversion conventions (Pakistan / global-first):
  - Size   : always stored in Marla  (1 Kanal = 20 Marla, 1 Marla = 272.25 sqft)
  - Price  : always stored as integer PKR  (1 crore = 10,000,000 ; 1 lakh = 100,000)
  - Country: ISO 3166-1 alpha-2 (PK default)
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# ── Unit constants ─────────────────────────────────────────────────────────────

MARLA_PER_KANAL  = 20
SQFT_PER_MARLA   = 272.25
CRORE_TO_PKR     = 10_000_000
LAKH_TO_PKR      = 100_000


# ── Property Search ────────────────────────────────────────────────────────────

class PropertySearchFilter(BaseModel):
    """
    Normalised property search parameters extracted from natural language.

    Input example : "5 marla house in DHA Lahore under 3 crore"
    Output example: {
        "city": "Lahore", "location": "DHA",
        "property_type": "residential",
        "area_marla": 5.0, "area_sqft": 1361.25,
        "price_max_pkr": 30000000, "currency": "PKR"
    }
    """
    city:          str            = Field(..., description="City name, title-cased")
    location:      str            = Field('',  description="Area / society e.g. 'DHA Phase 5'")
    property_type: Literal['plot', 'residential', 'commercial', ''] = ''
    area_marla:    Optional[float] = Field(None, gt=0,
                       description="Size normalised to Marla. 1 Kanal = 20 Marla.")
    area_sqft:     Optional[float] = Field(None, gt=0,
                       description="Derived from area_marla at 272.25 sqft/Marla.")
    price_min_pkr: Optional[int]  = Field(None, ge=0)
    price_max_pkr: Optional[int]  = Field(None, gt=0,
                       description="Max budget in PKR. 1 crore = 10,000,000.")
    currency:      str            = 'PKR'
    furnished:     Optional[Literal['furnished', 'semi_furnished', 'unfurnished']] = None
    purpose:       Literal['buy', 'rent', ''] = ''

    @field_validator('city')
    @classmethod
    def _title_city(cls, v: str) -> str:
        return v.strip().title()

    @model_validator(mode='after')
    def _derive_sqft(self):
        if self.area_marla and not self.area_sqft:
            self.area_sqft = round(self.area_marla * SQFT_PER_MARLA, 2)
        return self

    def to_tool_kwargs(self) -> dict:
        """Convert to kwargs accepted by apps.ai._tools_search.search_properties."""
        return {
            'city':                 self.city,
            'location':             self.location,
            'property_type':        self.property_type,
            'area_marla':           self.area_marla or 0.0,
            'max_price':        self.price_max_pkr or 0,
            'furnished':            self.furnished or '',
        }


# ── Scam / Fraud Check ─────────────────────────────────────────────────────────

class ScamCheckInput(BaseModel):
    """
    Input for the fraud/scam analysis tool.

    Captures structured signals extracted from the user's description
    before passing to run_fraud_check().
    """
    description:          str
    url:                  Optional[str] = None
    agent_name:           Optional[str] = None
    claimed_price:    Optional[int] = None
    advance_payment_demanded: bool = False
    documents_available:  bool = True
    urgency_signals:      list[str] = []

    @field_validator('description')
    @classmethod
    def _min_len(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Provide at least a brief description of the deal or situation.")
        return v

    def to_tool_kwargs(self) -> dict:
        """Convert to kwargs accepted by apps.ai._tools_fraud.run_fraud_check."""
        extra = ''
        if self.advance_payment_demanded:
            extra += ' advance payment demanded'
        if not self.documents_available:
            extra += ' no documents available'
        if self.url:
            extra += f' url: {self.url}'
        if self.agent_name:
            extra += f' agent: {self.agent_name}'
        return {'description': (self.description + extra).strip()}


# ── Tax Advice ─────────────────────────────────────────────────────────────────

class TaxAdviceInput(BaseModel):
    """
    Input for market-aware tax routing.

    advice_type selects which calculation to run:
      '7e'      → Section 7E Capital Value Tax (annual, Pakistan)
      'cgt'     → Capital Gains Tax on property sale
      'rental'  → Rental income tax
      'wht'     → Withholding tax on purchase/sale transaction
      'general' → Let the system pick based on context
    """
    fmv_pkr:          int   = Field(..., gt=0,
                          description="Fair Market Value in local currency (PKR for Pakistan)")
    filer_status:     Literal['filer', 'non_filer']
    advice_type:      Literal['7e', 'cgt', 'rental', 'wht', 'general'] = 'general'
    holding_years:    Optional[int]   = Field(None, ge=0,
                          description="Years property held — required for CGT calculation")
    is_self_occupied: bool            = False
    properties_count: int             = Field(1, ge=1)
    annual_rent_pkr:  Optional[int]   = Field(None, ge=0,
                          description="Annual rental income — required for rental tax calculation")
    country:          str             = 'PK'

    def to_tool_kwargs(self) -> dict:
        """Convert to kwargs accepted by apps.ai._tools_financial.calculate_7e_tax."""
        return {
            'fmv_pkr':          self.fmv_pkr,
            'filer_status':     self.filer_status,
            'properties_count': self.properties_count,
            'is_self_occupied': self.is_self_occupied,
            'country':          self.country,
        }


# ── Intent Classification Result ───────────────────────────────────────────────

# ── Loan Eligibility ──────────────────────────────────────────────────────────

class LoanEligibilityInput(BaseModel):
    """
    Extracted loan/mortgage eligibility parameters.

    Populated when IntentClassifier finds both income and loan amount in the message.
    Routes directly to check_loan_eligibility() bypassing the LLM.
    """
    monthly_income: int = Field(..., gt=0, description="Net monthly income in local currency")
    loan_amount:    int = Field(..., gt=0, description="Requested loan amount in local currency")
    tenure_years:   int = Field(20, ge=1, le=30)
    existing_emi:   int = Field(0, ge=0)
    scheme:         Literal['apna_ghar', 'conventional'] = 'conventional'
    country:        str = 'PK'

    def to_tool_kwargs(self) -> dict:
        return {
            'monthly_income_pkr': self.monthly_income,
            'loan_amount_pkr':    self.loan_amount,
            'tenure_years':       self.tenure_years,
            'existing_emi_pkr':   self.existing_emi,
            'scheme':             self.scheme,
            'country':            self.country,
        }


# ── Property Audit ─────────────────────────────────────────────────────────────

class AuditInput(BaseModel):
    """
    Extracted property audit parameters.

    Populated when IntentClassifier finds city + estimated value in the message.
    Routes directly to generate_property_audit() bypassing the LLM.
    """
    city:                str
    location:            str            = ''
    property_type:       str            = 'residential'
    estimated_value_pkr: int            = Field(..., gt=0)
    area_marla:          Optional[float] = Field(None, gt=0)
    description:         str            = ''

    def to_tool_kwargs(self) -> dict:
        return {
            'city':                 self.city,
            'location':             self.location,
            'property_type':        self.property_type,
            'estimated_value_pkr':  self.estimated_value_pkr,
            'area_marla':           self.area_marla or 0.0,
            'description':          self.description,
        }


# ── Intent Classification Result ───────────────────────────────────────────────

class IntentResult(BaseModel):
    """
    Output of the pre-LLM intent classifier.

    - confidence  : 0.0–1.0 (≥ 0.85 triggers direct Python tool routing)
    - search_filter / scam_input / tax_input / loan_input / audit_input :
      populated when confidence is high enough for direct routing
    - normalised_query : cleaned/normalised version of the original message
                         injected back to the LLM when confidence < 0.85
    """
    intent: Literal[
        'property_search', 'scam_check', 'tax_advice', 'loan_eligibility',
        'list_property', 'talk_to_agent', 'deal_lock', 'property_audit',
        'document_verify_text', 'general_query', 'off_topic', 'greeting',
    ]
    confidence:       float                            = Field(0.0, ge=0.0, le=1.0)
    language:         Literal['en', 'ur', 'mixed']    = 'en'
    extracted_params: dict                             = {}
    normalised_query: str                              = ''
    search_filter:    Optional[PropertySearchFilter]   = None
    scam_input:       Optional[ScamCheckInput]         = None
    tax_input:        Optional[TaxAdviceInput]         = None
    loan_input:       Optional[LoanEligibilityInput]   = None
    audit_input:      Optional[AuditInput]             = None
