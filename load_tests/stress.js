/**
 * Stress test — finds the breaking point by ramping to 200 VUs.
 * Measures where latency degrades and errors begin to appear.
 *
 * Interpret results:
 *   - Latency stays flat → system is not the bottleneck at this VU count
 *   - p95 crosses 1s     → DB/application layer saturating
 *   - Error rate rises   → connection pool or worker thread exhaustion
 *
 * Usage:
 *   k6 run stress.js
 *   k6 run -e BASE_URL=http://localhost:8000 -e JWT_TOKEN=<bearer> stress.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

export const options = {
  stages: [
    { duration: '2m', target: 20  },  // warm-up
    { duration: '3m', target: 100 },  // ramp to moderate load
    { duration: '3m', target: 200 },  // ramp to high load
    { duration: '5m', target: 200 },  // hold at peak
    { duration: '2m', target: 0   },  // ramp down
  ],
  thresholds: {
    // Stress test uses looser thresholds — goal is to observe, not pass/fail
    http_req_failed:   ['rate<0.05'],
    http_req_duration: ['p(95)<2000'],
  },
};

const BASE_URL  = __ENV.BASE_URL  || 'http://localhost:8000';
const JWT_TOKEN = __ENV.JWT_TOKEN || '';

const errorRate = new Rate('errors');

const AUTH_HEADERS = {
  Authorization:  `Bearer ${JWT_TOKEN}`,
  'Content-Type': 'application/json',
};

const CITIES = ['lahore', 'karachi', 'islamabad', 'rawalpindi'];
const TYPES  = ['residential', 'commercial', 'plot'];

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

let cachedPropertyId = null;

export default function () {
  const rand = Math.random();

  if (rand < 0.30) {
    // Property list — heaviest read (N+1 before fix, now prefetched)
    const res = http.get(`${BASE_URL}/api/v1/properties/`);
    errorRate.add(!check(res, { 'list 200': (r) => r.status === 200 }));

    if (!cachedPropertyId && res.status === 200) {
      try {
        const data = JSON.parse(res.body);
        if (data.results && data.results.length > 0) cachedPropertyId = data.results[0].id;
      } catch (_) {}
    }

  } else if (rand < 0.55) {
    // Filtered search — exercises city + property_type indexes
    const res = http.get(
      `${BASE_URL}/api/v1/properties/?city=${randomItem(CITIES)}&type=${randomItem(TYPES)}`,
    );
    errorRate.add(!check(res, { 'search 200': (r) => r.status === 200 }));

  } else if (rand < 0.70) {
    // Price range filter — exercises price index
    const min = Math.floor(randomBetween(1_000_000, 10_000_000));
    const max = min + Math.floor(randomBetween(5_000_000, 40_000_000));
    const res = http.get(`${BASE_URL}/api/v1/properties/?min_price=${min}&max_price=${max}`);
    errorRate.add(!check(res, { 'price 200': (r) => r.status === 200 }));

  } else if (rand < 0.80) {
    // Property detail
    if (cachedPropertyId) {
      const res = http.get(`${BASE_URL}/api/v1/properties/${cachedPropertyId}/`);
      errorRate.add(!check(res, { 'detail 200': (r) => r.status === 200 }));
    }

  } else if (rand < 0.90) {
    // Health check
    const res = http.get(`${BASE_URL}/health/`);
    errorRate.add(!check(res, { 'health 200': (r) => r.status === 200 }));

  } else {
    // Authenticated leads (if token provided)
    if (JWT_TOKEN) {
      const res = http.get(`${BASE_URL}/api/v1/leads/`, { headers: AUTH_HEADERS });
      errorRate.add(!check(res, { 'leads 200': (r) => r.status === 200 }));
    } else {
      const res = http.get(`${BASE_URL}/api/v1/properties/?ordering=-ai_score`);
      errorRate.add(!check(res, { 'sorted 200': (r) => r.status === 200 }));
    }
  }

  sleep(randomBetween(0.5, 2));
}
