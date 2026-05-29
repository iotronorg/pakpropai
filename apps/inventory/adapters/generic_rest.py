import logging

import requests

from apps.inventory.adapters.base import ExternalPlatformAdapter

logger = logging.getLogger(__name__)


class GenericRestAdapter(ExternalPlatformAdapter):
    """
    Configurable adapter for any REST platform.
    Requires connection.field_mappings to define the mapping.
    """

    def poll_listings(self, limit: int = 100) -> list:
        try:
            resp = requests.get(
                f'{self.config.base_url}/listings',
                headers={'Authorization': f'Bearer {self.config.api_key}'},
                params={'limit': limit},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get('results', data.get('data', data.get('items', [])))
        except Exception as exc:
            logger.warning('GenericRestAdapter.poll_listings failed: %s', exc)
            return []

    def map_listing(self, raw: dict) -> dict:
        if not self.config.field_mappings:
            return raw
        return {internal: raw[ext] for internal, ext in self.config.field_mappings.items() if ext in raw}

    def push_update(self, property_id: str, delta: dict) -> bool:
        try:
            resp = requests.patch(
                f'{self.config.base_url}/listings/{property_id}',
                json=delta,
                headers={'Authorization': f'Bearer {self.config.api_key}'},
                timeout=15,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning('GenericRestAdapter.push_update failed: %s', exc)
            return False
