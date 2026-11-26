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


@shared_task(name='apps.core.tasks.purge_idempotency_records', ignore_result=True)
def purge_idempotency_records() -> int:
    """Delete expired Idempotency-Key records (ADR-018)."""
    from apps.core.idempotency import purge_expired
    return purge_expired()


@shared_task(name='apps.core.tasks.verify_audit_chain', ignore_result=True)
def verify_audit_chain() -> dict:
    """Daily check of the unified audit trail (T-3070, ADR-060). The head goes to the logs, outside the DB."""
    import logging

    from apps.core import audit
    from apps.core.metrics import audit_chain_ok
    log = logging.getLogger('apps.core.audit')
    v = audit.verify()
    audit_chain_ok.set(1 if v.ok else 0)
    if v.ok:
        log.info('audit.chain.ok checked=%d head_seq=%s head_hash=%s', v.checked, v.head_seq, v.head_hash)
    else:
        log.error('audit.chain.broken at=%s reason=%s checked=%d', v.broken_at, v.reason, v.checked)
    return v.as_dict()
