import logging

import requests

from apps.inventory.adapters.base import ExternalPlatformAdapter

logger = logging.getLogger(__name__)

_DEFAULT_FIELD_MAP = {
    'title':    'displayAddress',
    'price':    'price.amount',
    'city':     'location.town',
    'location': 'location.displayName',
    'area_sqm': 'floorplanSqft',
}

_BASE_URL = 'https://api.rightmove.co.uk/api'


class RightmoveAdapter(ExternalPlatformAdapter):

    def poll_listings(self, limit: int = 100) -> list:
        try:
            base = self.config.base_url or _BASE_URL
            resp = requests.get(
                f'{base}/buy/search',
                params={
                    'apiApplication': self.config.api_key,
                    'numberOfPropertiesRequested': limit,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get('properties', [])
        except Exception as exc:
            logger.warning('RightmoveAdapter.poll_listings failed: %s', exc)
            return []

    def map_listing(self, raw: dict) -> dict:
        defaults = _DEFAULT_FIELD_MAP
        mappings = self.config.field_mappings if self.config.field_mappings else defaults
        result = {}
        for internal, ext in mappings.items():
            parts = ext.split('.')
            val = raw
            for part in parts:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break
            if val is not None:
                result[internal] = val
        return result

    def push_update(self, property_id: str, delta: dict) -> bool:
        try:
            base = self.config.base_url or _BASE_URL
            resp = requests.put(
                f'{base}/property/{property_id}',
                json=delta,
                headers={'X-Api-Key': self.config.api_key},
                timeout=15,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning('RightmoveAdapter.push_update failed: %s', exc)
            return False
