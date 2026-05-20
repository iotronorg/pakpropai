import logging
from apps.core.context import get_organization_id, get_lead_id


class TraceContextFilter(logging.Filter):
    """
    Injects organization_id and lead_id from the per-request context vars into
    every LogRecord so that structured log sinks (stdout JSON, Sentry, etc.) can
    correlate entries by tenant and lead without manual field threading.

    Attached to all handlers in the LOGGING config. Returns '-' when a field
    has no value so that log lines are always parseable.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.organization_id = get_organization_id() or '-'
        record.lead_id = get_lead_id() or '-'
        return True
