import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')

app = Celery('pakprop')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

# ── Metrics export (ADR-050) ──────────────────────────────────────────────────
# AI replies to WhatsApp, inbound webhook processing and outbox deliveries run in
# the worker, so the worker serves its own /metrics. Prefork pool processes write
# to PROMETHEUS_MULTIPROC_DIR and the parent adds them up.

from celery.signals import worker_process_shutdown, worker_ready  # noqa: E402


@worker_ready.connect
def start_metrics_server(sender=None, **kwargs):
    import logging
    from django.conf import settings
    log = logging.getLogger('config.celery')
    port = int(getattr(settings, 'WORKER_METRICS_PORT', 0) or 0)
    if port <= 0:
        return None
    from apps.core.metrics import metrics_registry, multiprocess_dir
    if not multiprocess_dir():
        log.warning('worker metrics: PROMETHEUS_MULTIPROC_DIR is not set, so samples from prefork pool '
                    'processes are not exported (fine with --pool solo/threads); set it to an empty '
                    'directory per container')
    from prometheus_client import start_http_server
    try:
        start_http_server(port, addr=getattr(settings, 'WORKER_METRICS_ADDR', '0.0.0.0'),  # nosec B104
                          registry=metrics_registry())
    except OSError:
        # Another process on this host already serves the port.
        log.warning('worker metrics: port %s is in use; not serving from this worker', port)
        return None
    log.info('worker metrics: serving on :%s', port)
    return port


@worker_process_shutdown.connect
def mark_metrics_process_dead(pid=None, **kwargs):
    from apps.core.metrics import multiprocess_dir
    if multiprocess_dir():
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(pid or os.getpid())
