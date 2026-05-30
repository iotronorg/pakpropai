"""
VerificationProvider — abstract base for document / identity verification providers.

Concrete providers: JumioVerificationProvider (AE), OnfidoVerificationProvider (GB),
StripeIdentityProvider (US). For PK, the existing internal AI OCR is used directly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VerificationSession:
    """Returned by create_session() — contains the URL to send the user to."""
    session_id:  str
    session_url: str
    provider:    str
    expires_at:  Optional[str] = None


@dataclass
class VerificationResult:
    """Returned by parse_webhook() or poll_result() once verification is complete."""
    status:           str           # approved | declined | pending | error
    confidence:       float         # 0.0–1.0
    extracted_fields: dict          = field(default_factory=dict)
    red_flags:        list          = field(default_factory=list)
    error:            Optional[str] = None


class VerificationProvider(ABC):
    supported: bool = True
    name:      str  = 'base'

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True iff the required credentials are present in settings."""

    @abstractmethod
    def create_session(
        self,
        user_id:      str,
        doc_type:     str = 'passport',
        redirect_url: str = '',
    ) -> VerificationSession:
        """Start a hosted verification session; return URL to redirect the user to."""

    @abstractmethod
    def parse_webhook(self, payload: dict) -> tuple[str, VerificationResult]:
        """
        Parse an inbound webhook payload.
        Returns (session_id, VerificationResult).
        """


class UnsupportedProvider(VerificationProvider):
    """Returned for countries with no configured verification provider."""
    supported = False
    name      = 'unsupported'

    def __init__(self, country: str = ''):
        self.country = country

    def is_configured(self) -> bool:
        return False

    def create_session(self, user_id: str, doc_type: str = 'passport', redirect_url: str = '') -> VerificationSession:
        raise NotImplementedError(f'ID verification not supported for country: {self.country}')

    def parse_webhook(self, payload: dict) -> tuple[str, VerificationResult]:
        raise NotImplementedError
