"""Outbox dispatch/relay tasks (ADR-014). Messages carry only the outbox row id."""
from celery import shared_task


@shared_task(ignore_result=True, acks_late=True)
def dispatch_outbox_message(msg_id: str) -> str:
    from apps.core import outbox
    return outbox.dispatch(msg_id)


@shared_task(ignore_result=True)
def relay_outbox() -> int:
    from apps.core import outbox
    return outbox.relay_due()


@shared_task(ignore_result=True)
def purge_outbox() -> int:
    from apps.core import outbox
    return outbox.purge()
