from celery import shared_task


@shared_task(name='apps.locations.tasks.link_unmatched_listings')
def link_unmatched_listings(limit: int = 2000):
    """Hourly: link listings written outside the API (AI tool, scrapers, admin) to locations."""
    from .matching import link_unmatched
    return link_unmatched(limit=limit)
