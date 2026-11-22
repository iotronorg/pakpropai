"""
Price history attribution (T-1070c, ADR-023).

``Property.save()`` records a ``PriceHistory`` row whenever the price terms
change. Who and what caused the change is supplied by the caller explicitly:

    with price_change_context(user=request.user, source='api'):
        serializer.save()

Outside a context the row is attributed to no user with source 'system'.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_actor: ContextVar = ContextVar('price_change_actor', default=None)
_source: ContextVar[str] = ContextVar('price_change_source', default='system')


@contextmanager
def price_change_context(user=None, source: str = 'api'):
    t1 = _actor.set(user if getattr(user, 'is_authenticated', False) else None)
    t2 = _source.set(source)
    try:
        yield
    finally:
        _actor.reset(t1)
        _source.reset(t2)


def current_actor():
    return _actor.get()


def current_source() -> str:
    return _source.get()
