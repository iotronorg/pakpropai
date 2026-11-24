from celery import shared_task


@shared_task
def send_saved_search_alerts():
    from .alerts import run_due
    return run_due()


@shared_task
def purge_stale_preferences():
    from .services import purge
    return purge()
