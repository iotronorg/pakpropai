#!/usr/bin/env python3
"""
Post-deploy health gate used by .github/workflows/ci.yml (deploy-prod).

Polls HEALTH_URL until it returns HTTP 200 a required number of consecutive
times, or the deadline passes. On failure it prints the ECS service's deployment
state (if boto3 + credentials are available) and exits 1 so the workflow fails.
Rollback itself is performed by the ECS deployment circuit breaker configured
on the service — this script does not change infrastructure.

Env:
  HEALTH_URL            required, e.g. https://api.example.com/health/
  ECS_CLUSTER, ECS_SERVICE, AWS_REGION   optional, for diagnostics
  HEALTH_TIMEOUT_S      overall deadline (default 300)
  HEALTH_REQUIRED_OK    consecutive 200s required (default 3)
  HEALTH_INTERVAL_S     delay between probes (default 10)
"""
import os
import sys
import time

import requests


def _diagnose() -> None:
    cluster, service = os.environ.get('ECS_CLUSTER'), os.environ.get('ECS_SERVICE')
    if not (cluster and service):
        return
    try:
        import boto3
        ecs = boto3.client('ecs', region_name=os.environ.get('AWS_REGION'))
        svc = ecs.describe_services(cluster=cluster, services=[service])['services'][0]
        for d in svc.get('deployments', []):
            print(f"deployment {d.get('status')} rollout={d.get('rolloutState')} "
                  f"running={d.get('runningCount')}/{d.get('desiredCount')} "
                  f"taskdef={d.get('taskDefinition', '').rsplit('/', 1)[-1]}")
    except Exception as exc:  # diagnostics only
        print(f"could not describe ECS service: {exc.__class__.__name__}")


def main() -> int:
    url = os.environ.get('HEALTH_URL', '').strip()
    if not url:
        print('HEALTH_URL is not set')
        return 1
    deadline = time.monotonic() + int(os.environ.get('HEALTH_TIMEOUT_S', '300'))
    required = int(os.environ.get('HEALTH_REQUIRED_OK', '3'))
    interval = int(os.environ.get('HEALTH_INTERVAL_S', '10'))

    ok = 0
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            status = resp.status_code
        except requests.RequestException as exc:
            status = f'error:{exc.__class__.__name__}'
        if status == 200:
            ok += 1
            print(f'health ok ({ok}/{required})')
            if ok >= required:
                return 0
        else:
            ok = 0
            print(f'health check failed: {status}')
        time.sleep(interval)

    print('health gate FAILED — deadline reached')
    _diagnose()
    return 1


if __name__ == '__main__':
    sys.exit(main())
