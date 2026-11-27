"""MVP criterion 13: staging deploys automatically, production by approval, same image (ADR-053)."""
import ast
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from django.test import SimpleTestCase, TestCase, override_settings

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / '.github' / 'workflows'
SCRIPT = ROOT / 'scripts' / 'deploy' / 'remote_deploy.sh'
TAG = '0123456789ab'


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


class PipelineShapeTest(SimpleTestCase):
    def setUp(self):
        self.ci = workflow('ci.yml')
        self.jobs = self.ci['jobs']

    def test_production_needs_staging_gate_and_build(self):
        prod = self.jobs['deploy-production']
        self.assertEqual(set(prod['needs']), {'build', 'deploy-staging', 'production-gate'})
        self.assertEqual(prod['with']['environment'], 'production')
        self.assertEqual(self.jobs['deploy-staging']['with']['environment'], 'staging')
        # Both deploy the image built once for this commit.
        for job in ('deploy-staging', 'deploy-production'):
            self.assertEqual(self.jobs[job]['with']['image_tag'], '${{ needs.build.outputs.image_tag }}')
            self.assertEqual(self.jobs[job]['uses'], './.github/workflows/deploy.yml')

    def test_build_runs_after_checks_on_main_only(self):
        build = self.jobs['build']
        self.assertEqual(set(build['needs']), {'test', 'security'})
        self.assertIn("refs/heads/main", build['if'])
        self.assertIn("github.event_name == 'push'", build['if'])

    def test_main_is_never_cancelled(self):
        self.assertIn("github.ref != 'refs/heads/main'", str(self.ci['concurrency']['cancel-in-progress']))
        deploy = workflow('deploy.yml')['jobs']['deploy']
        self.assertIs(deploy['concurrency']['cancel-in-progress'], False)
        self.assertEqual(deploy['environment']['name'], '${{ inputs.environment }}')

    def test_gate_checks_required_reviewers(self):
        steps = ' '.join(s.get('run', '') for s in self.jobs['production-gate']['steps'])
        self.assertIn('required_reviewers', steps)
        self.assertIn('exit 1', steps)

    def test_ci_checks(self):
        text = (WORKFLOWS / 'ci.yml').read_text()
        for check in ('makemigrations --check', 'run_ai_evals', 'pip-audit', 'bandit', 'trufflehog', 'pytest',
                      'promtool'):
            self.assertIn(check, text)
        self.assertNotIn('|| true', text)                    # no step that passes whatever happens
        for uses in re.findall(r'uses: (\S+)', text):
            self.assertNotRegex(uses, r'@(main|master)$', uses)
        self.assertNotIn('ecs', text.lower())                # owner decision: Docker over SSH

    def test_ssh_host_keys_are_checked(self):
        text = (WORKFLOWS / 'deploy.yml').read_text()
        self.assertNotIn('StrictHostKeyChecking=no', text)
        self.assertEqual(text.count('ssh -i') + text.count('scp -i'),
                         text.count('StrictHostKeyChecking=yes'))
        self.assertIn('--password-stdin', text)


# ── The script that runs on the server, with a fake docker ───────────────────

FAKE_DOCKER = r"""#!/bin/sh
echo "IMAGE_TAG=$IMAGE_TAG $*" >> "$LOG"
case "$*" in
  *check_release*) [ -z "$FAIL_RELEASE_CHECK" ] || exit 1 ;;
  *" run --rm --no-deps migrate"*) [ -z "$FAIL_MIGRATE" ] || exit 1 ;;
  *" exec -T web "*)
    if [ "$FAKE_VERSION" = "$IMAGE_TAG" ]; then echo "$IMAGE_TAG ok"; exit 0; fi
    echo "$FAKE_VERSION ok"; exit 1 ;;
esac
exit 0
"""


class RemoteDeployScriptTest(SimpleTestCase):
    def run_script(self, previous=None, **env):
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'docker').mkdir()
        (tmp / 'docker' / 'docker-compose.prod.yml').write_text('services: {}\n')
        (tmp / '.env').write_text('X=1\n')
        (tmp / 'remote_deploy.sh').write_text(SCRIPT.read_text())
        if previous:
            (tmp / '.deployed_tag').write_text(previous + '\n')
        bin_dir = tmp / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'docker'
        fake.write_text(FAKE_DOCKER)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        log = tmp / 'docker.log'
        full_env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'LOG': str(log), 'IMAGE_TAG': TAG,
                    'WEB_IMAGE': 'ghcr.io/acme/app/web', 'WORKER_IMAGE': 'ghcr.io/acme/app/worker',
                    'FAKE_VERSION': TAG, 'HEALTH_TIMEOUT_S': '2', 'HEALTH_INTERVAL_S': '0', **env}
        r = subprocess.run(['sh', str(tmp / 'remote_deploy.sh')], env=full_env, capture_output=True, text=True,
                           timeout=60)
        # One entry per docker call (the health probe's inline Python spans several lines).
        calls = [c for c in log.read_text().splitlines() if c.startswith('IMAGE_TAG=')] if log.exists() else []
        tag = (tmp / '.deployed_tag').read_text().strip() if (tmp / '.deployed_tag').exists() else None
        return r, calls, tag

    def test_healthy_deploy_records_the_tag(self):
        r, calls, tag = self.run_script(previous='aaaaaaaaaaaa')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tag, TAG)
        order = [next(i for i, c in enumerate(calls) if p in c)
                 for p in (' pull ', ' up -d --no-build --wait db redis', 'run --rm --no-deps migrate',
                           'check_release', ' up -d --no-build web worker')]
        self.assertEqual(order, sorted(order))
        bad = [c for c in calls if not c.startswith(f'IMAGE_TAG={TAG} compose -p realtron --env-file .env -f ')]
        self.assertEqual(bad, [])

    def test_failed_migration_switches_nothing(self):
        r, calls, tag = self.run_script(previous='aaaaaaaaaaaa', FAIL_MIGRATE='1')
        self.assertEqual(r.returncode, 1)
        self.assertIn('migration failed', r.stderr)
        self.assertFalse(any(' up -d --no-build web' in c for c in calls))
        self.assertEqual(tag, 'aaaaaaaaaaaa')

    def test_failed_release_check_switches_nothing(self):
        r, calls, tag = self.run_script(previous='aaaaaaaaaaaa', FAIL_RELEASE_CHECK='1')
        self.assertEqual(r.returncode, 1)
        self.assertIn('release check failed', r.stderr)
        self.assertTrue(any('run --rm --no-deps migrate-uk' in c for c in calls))
        self.assertFalse(any(' up -d --no-build web' in c for c in calls))
        self.assertEqual(tag, 'aaaaaaaaaaaa')

    def test_release_check_is_skipped_by_builds_without_it(self):
        # A rollback to a build older than T-6040 must still deploy.
        cmd = next(line for line in SCRIPT.read_text().splitlines() if 'help check_release' in line)
        self.assertIn('skipped', cmd)
        r = subprocess.run(['sh', '-c', cmd.strip().rstrip('\\').strip().strip("'").replace('python manage.py', 'false')],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn('skipped', r.stdout)

    def test_unhealthy_build_is_rolled_back(self):
        r, calls, tag = self.run_script(previous='aaaaaaaaaaaa', FAKE_VERSION='something-else')
        self.assertEqual(r.returncode, 1)
        self.assertIn('rolling back to aaaaaaaaaaaa', r.stderr)
        self.assertTrue(calls[-1].startswith('IMAGE_TAG=aaaaaaaaaaaa compose'))
        self.assertIn(' up -d --no-build web worker worker-hr beat', calls[-1])
        self.assertEqual(tag, 'aaaaaaaaaaaa')

    def test_rejects_unsafe_input(self):
        for env in ({'IMAGE_TAG': 'latest'}, {'IMAGE_TAG': f"{TAG}';rm -rf /;'"},
                    {'WEB_IMAGE': 'ghcr.io/x;reboot'}, {'HEALTH_HOST': 'a b'}):
            with self.subTest(env):
                r, calls, _ = self.run_script(**env)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(calls, [])


# ── /health/ in production ───────────────────────────────────────────────────

def _prod_setting(name):
    tree = ast.parse((ROOT / 'config' / 'settings' / 'prod.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == name:
            return ast.literal_eval(node.value)
    raise KeyError(name)


@override_settings(ALLOWED_HOSTS=['*'])
class HealthInProductionTest(TestCase):
    def test_version_is_reported(self):
        with override_settings(APP_VERSION=TAG):
            self.assertEqual(self.client.get('/health/').json()['version'], TAG)

    def test_internal_probes_are_not_redirected_to_https(self):
        """KI-117: production redirected the healthcheck and the Prometheus scrape to https://web:8000."""
        with override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=_prod_setting('SECURE_REDIRECT_EXEMPT')):
            self.assertNotEqual(self.client.get('/health/', HTTP_HOST='web:8000').status_code, 301)
            self.assertNotEqual(self.client.get('/metrics/', HTTP_HOST='web:8000').status_code, 301)
            self.assertEqual(self.client.get('/api/v1/theme/', HTTP_HOST='web:8000').status_code, 301)

    def test_errors_do_not_leak_details(self):
        secret = 'connection to db-host.internal user realtron failed'
        with patch('apps.core.views.cache', MagicMock(set=MagicMock(side_effect=RuntimeError(secret)))), \
                override_settings(DEBUG=False):
            body = self.client.get('/health/').json()
        self.assertEqual(body['checks']['redis'], {'status': 'error', 'detail': 'RuntimeError'})


# ── Smoke test used after each deploy ────────────────────────────────────────

class SmokeTestScriptTest(SimpleTestCase):
    def load(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        self.addCleanup(sys.path.remove, str(ROOT / 'scripts'))
        import importlib
        import smoke_test
        return importlib.reload(smoke_test)

    def resp(self, status, body=None):
        r = MagicMock(status_code=status)
        r.json.return_value = body or {}
        return r

    def test_health_needs_ok_and_the_deployed_version(self):
        st = self.load()
        with patch.dict(os.environ, {'SMOKE_EXPECT_VERSION': TAG}):
            for status, body, ok in ((200, {'status': 'ok', 'version': TAG}, True),
                                     (200, {'status': 'ok', 'version': 'old'}, False),
                                     (503, {'status': 'degraded', 'version': TAG}, False)):
                with self.subTest(status=status, body=body), patch.object(st.requests, 'get',
                                                                          return_value=self.resp(status, body)):
                    self.assertIs(st.probe_health(), ok)

    def test_unsigned_webhook_must_be_refused(self):
        st = self.load()
        with patch.object(st.requests, 'post', return_value=self.resp(200)):
            self.assertFalse(st.probe_webhook_post())
            with patch.dict(os.environ, {'SMOKE_ALLOW_UNSIGNED': '1'}):
                self.assertTrue(st.probe_webhook_post())
        with patch.object(st.requests, 'post', return_value=self.resp(403)):
            self.assertTrue(st.probe_webhook_post())


class EntrypointTest(SimpleTestCase):
    def test_web_entrypoint_runs_a_given_command(self):
        """KI-119: the migrate services ran the server instead of `manage.py migrate`."""
        r = subprocess.run(['sh', str(ROOT / 'docker' / 'entrypoint.web.sh'), 'echo', 'ran the command'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ran the command'))

    def test_images_create_the_directories_they_write(self):
        for name, dirs in (('Dockerfile.web', ('/app/staticfiles', '/app/media')), ('Dockerfile.worker', ('/app/media',))):
            text = (ROOT / 'docker' / name).read_text()
            for d in dirs:
                self.assertRegex(text, rf'mkdir -p [^\n]*{d}', name)
            self.assertIn('chown realtron:realtron /app ', text)
            self.assertLess(text.index('chown realtron:realtron /app '), text.index('USER realtron'))
