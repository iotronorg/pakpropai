from celery import shared_task


@shared_task
def notify_overdue_moderation():
    from .services import notify_overdue
    return notify_overdue()
