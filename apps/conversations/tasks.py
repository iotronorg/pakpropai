from celery import shared_task


@shared_task(ignore_result=True)
def purge_conversations() -> int:
    """Delete conversations past their market's retention (T-2020)."""
    from .store import purge
    return purge()
