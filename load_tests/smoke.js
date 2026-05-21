/**
 * Smoke test — 5 VUs, 30 seconds.
 * Verifies all key endpoints respond correctly before a full load run.
 *
 * Usage:
 *   k6 run smoke.js
 *   k6 run -e BASE_URL=http://localhost:8000 -e JWT_TOKEN=<bearer> smoke.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 5,
  duration: '30s',
  thresholds: {
    http_req_failed:   ['rate<0.01'],
    http_req_duration: ['p(95)<500'],
  },
};

const BASE_URL  = __ENV.BASE_URL  || 'http://localhost:8000';
const JWT_TOKEN = __ENV.JWT_TOKEN || '';

function authHeaders() {
  return JWT_TOKEN
    ? { Authorization: `Bearer ${JWT_TOKEN}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' };
}

export default function () {
  // Health check
  const health = http.get(`${BASE_URL}/health/`);
  check(health, { 'health 200': (r) => r.status === 200 });

  // Property list (public)
  const list = http.get(`${BASE_URL}/api/v1/properties/`);
  check(list, {
    'property list 200': (r) => r.status === 200,
    'property list has results': (r) => {
      try { return JSON.parse(r.body).results !== undefined; } catch { return false; }
    },
  });

  // Property search by city (public)
  const search = http.get(`${BASE_URL}/api/v1/properties/?city=lahore&type=residential`);
  check(search, { 'property search 200': (r) => r.status === 200 });

  // Price-range filter (uses new price index)
  const priceFilter = http.get(`${BASE_URL}/api/v1/properties/?min_price=5000000&max_price=50000000`);
  check(priceFilter, { 'price filter 200': (r) => r.status === 200 });

  if (JWT_TOKEN) {
    const leads = http.get(`${BASE_URL}/api/v1/leads/`, { headers: authHeaders() });
    check(leads, { 'leads 200': (r) => r.status === 200 });

    const stats = http.get(`${BASE_URL}/api/v1/leads/stats/`, { headers: authHeaders() });
    check(stats, { 'leads stats 200': (r) => r.status === 200 });
  }

  sleep(1);
}
