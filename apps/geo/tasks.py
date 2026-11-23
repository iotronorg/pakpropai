from celery import shared_task


@shared_task(name='apps.geo.tasks.geocode_locations')
def geocode_locations(limit: int = 300):
    """Daily: geocode new location nodes and refresh centroids older than the cache limit."""
    from .geocoding import run
    return run(limit=limit)
