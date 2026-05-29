import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AdapterConfig:
    platform: str
    base_url: str
    api_key: str
    api_secret: str
    refresh_interval_mins: int = 15
    field_mappings: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    platform: str
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)


class ExternalPlatformAdapter(ABC):

    def __init__(self, config: AdapterConfig):
        self.config = config

    @abstractmethod
    def poll_listings(self, limit: int = 100) -> list:
        """Fetch listings from the external platform. Must be fail-open (return [] on error)."""

    @abstractmethod
    def map_listing(self, raw: dict) -> dict:
        """Map platform-specific listing dict to Property field names."""

    @abstractmethod
    def push_update(self, property_id: str, delta: dict) -> bool:
        """Push a property update to the external platform. Returns True on success."""

    def _apply_field_mappings(self, raw: dict, defaults: dict) -> dict:
        mappings = self.config.field_mappings if self.config.field_mappings else defaults
        result = {}
        for internal_field, external_field in mappings.items():
            if external_field in raw:
                result[internal_field] = raw[external_field]
        return result
