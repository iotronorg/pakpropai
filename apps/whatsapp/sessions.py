import json
from django.core.cache import cache


class SessionManager:
    """
    Tiny FSM stored in Redis (Django cache).
    Key:   wa:session:{phone}
    Value: {state, context, message_count}
    TTL:   30 minutes
    """
    PREFIX = 'wa:session:'
    TTL    = 60 * 30

    @classmethod
    def _key(cls, phone): return f"{cls.PREFIX}{phone}"

    @classmethod
    def get(cls, phone: str) -> dict:
        data = cache.get(cls._key(phone))
        if data is None:
            return {'state': 'IDLE', 'context': {}, 'message_count': 0}
        return data

    @classmethod
    def set(cls, phone: str, session: dict):
        cache.set(cls._key(phone), session, cls.TTL)

    @classmethod
    def update(cls, phone: str, **kwargs):
        s = cls.get(phone)
        s.update(kwargs)
        cls.set(phone, s)
        return s

    @classmethod
    def clear(cls, phone: str):
        cache.delete(cls._key(phone))