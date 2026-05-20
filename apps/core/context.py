"""
Per-request trace context using Python contextvars.

Set organization_id once in TraceContextMiddleware (from request.organization).
Set lead_id from individual views or services when a lead is identified:

    from apps.core.context import set_trace_context
    set_trace_context(lead_id=str(lead.pk))

Every log record emitted after that point will carry both IDs automatically
via TraceContextFilter.  Context is cleared at request teardown.

Celery tasks run outside the request cycle — context vars default to ''.
Tasks that need tracing should call set_trace_context() explicitly.
"""
from contextvars import ContextVar
from typing import Any

_org_id: ContextVar[str] = ContextVar('organization_id', default='')
_lead_id: ContextVar[str] = ContextVar('lead_id', default='')

_UNSET: Any = object()  # sentinel — distinguishes "not provided" from "set to empty"


def set_trace_context(*, organization_id: Any = _UNSET, lead_id: Any = _UNSET) -> None:
    if organization_id is not _UNSET:
        _org_id.set(str(organization_id) if organization_id else '')
    if lead_id is not _UNSET:
        _lead_id.set(str(lead_id) if lead_id else '')


def clear_trace_context() -> None:
    _org_id.set('')
    _lead_id.set('')


def get_organization_id() -> str:
    return _org_id.get()


def get_lead_id() -> str:
    return _lead_id.get()
