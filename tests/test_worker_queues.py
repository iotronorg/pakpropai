"""Every queue a task is routed to has a worker that reads it (session 23; found by scripts/dev/e2e.sh)."""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)


class WorkerQueueTest(SimpleTestCase):
    def test_single_worker_deployments_read_every_queue(self):
        queues = {r['queue'] for r in settings.CELERY_TASK_ROUTES.values()} | {settings.CELERY_TASK_DEFAULT_QUEUE, 'outbound'}
        for name, pattern in (('Procfile', r'^worker:.*-Q ([\w,-]+)'),
                              ('docker/entrypoint.worker.sh', r'CELERY_QUEUES:-(default[\w,-]+)\}'),
                              ('docker-compose.yml', r'celery -A config worker.*-Q ([\w,-]+)')):
            found = re.search(pattern, (ROOT / name).read_text(), re.M)
            self.assertIsNotNone(found, name)
            self.assertLessEqual(queues, set(found[1].split(',')), name)

    def test_production_workers_together_read_every_queue(self):
        text = (ROOT / 'docker/docker-compose.prod.yml').read_text()
        read = {q for line in re.findall(r'CELERY_QUEUES: "([^"]+)"', text) for q in line.split(',')}
        self.assertLessEqual({r['queue'] for r in settings.CELERY_TASK_ROUTES.values()}, read)
