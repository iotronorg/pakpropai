import logging

import requests

from apps.inventory.adapters.base import ExternalPlatformAdapter

logger = logging.getLogger(__name__)

_DEFAULT_FIELD_MAP = {
    'title':    'name',
    'price':    'price',
    'city':     'city',
    'location': 'location',
    'area_sqm': 'area',
}

_BASE_URL = 'https://api.zameen.com/v1'


class ZameenAdapter(ExternalPlatformAdapter):

    def poll_listings(self, limit: int = 100) -> list:
        try:
            base = self.config.base_url or _BASE_URL
            resp = requests.get(
                f'{base}/properties',
                params={'api_key': self.config.api_key, 'limit': limit},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get('results', [])
        except Exception as exc:
            logger.warning('ZameenAdapter.poll_listings failed: %s', exc)
            return []

    def map_listing(self, raw: dict) -> dict:
        defaults = _DEFAULT_FIELD_MAP
        mappings = self.config.field_mappings if self.config.field_mappings else defaults
        return {internal: raw[ext] for internal, ext in mappings.items() if ext in raw}

    def push_update(self, property_id: str, delta: dict) -> bool:
        try:
            base = self.config.base_url or _BASE_URL
            resp = requests.patch(
                f'{base}/properties/{property_id}',
                json=delta,
                headers={'X-Api-Key': self.config.api_key},
                timeout=15,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning('ZameenAdapter.push_update failed: %s', exc)
            return False
