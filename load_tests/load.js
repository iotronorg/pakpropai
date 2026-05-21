/**
 * Load test — staged ramp simulating realistic production traffic.
 * Target: 50 concurrent VUs sustained over 5 minutes.
 *
 * Traffic mix (approximate):
 *   40%  Property browse/search  (public, read-heavy)
 *   25%  Property detail          (public, single-object fetch)
 *   15%  Property price/type filter
 *   10%  Health check             (load-balancer probe simulation)
 *   10%  Authenticated CRM ops    (leads list + stats; requires JWT_TOKEN)
 *
 * Usage:
 *   k6 run load.js
 *   k6 run -e BASE_URL=http://localhost:8000 -e JWT_TOKEN=<bearer> load.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

export const options = {
  stages: [
    { duration: '2m', target: 10 },  // warm-up
    { duration: '3m', target: 50 },  // ramp to target
    { duration: '5m', target: 50 },  // hold
    { duration: '1m', target: 0  },  // ramp down
  ],
  thresholds: {
    http_req_failed:   ['rate<0.01'],
    http_req_duration: ['p(95)<800', 'p(99)<1500'],
  },
};

const BASE_URL  = __ENV.BASE_URL  || 'http://localhost:8000';
const JWT_TOKEN = __ENV.JWT_TOKEN || '';

const errorRate       = new Rate('errors');
const propertyLatency = new Trend('property_list_duration', true);
const searchLatency   = new Trend('property_search_duration', true);

const AUTH_HEADERS = {
  Authorization:  `Bearer ${JWT_TOKEN}`,
  'Content-Type': 'application/json',
};

// Cities sampled from the default city-code map.
const CITIES = ['lahore', 'karachi', 'islamabad', 'rawalpindi', 'faisalabad', 'multan'];
const TYPES  = ['residential', 'commercial', 'plot'];

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

let cachedPropertyId = null;

function browseProperties() {
  const res = http.get(`${BASE_URL}/api/v1/properties/`, { tags: { endpoint: 'property_list' } });
  const ok  = check(res, {
    'browse 200':       (r) => r.status === 200,
    'browse has body':  (r) => r.body && r.body.length > 0,
  });
  errorRate.add(!ok);
  propertyLatency.add(res.timings.duration);

  // Cache a property ID from the first successful list response.
  if (!cachedPropertyId && res.status === 200) {
    try {
      const data = JSON.parse(res.body);
      if (data.results && data.results.length > 0) {
        cachedPropertyId = data.results[0].id;
      }
    } catch (_) {}
  }
}

function searchByCity() {
  const city = randomItem(CITIES);
  const type = randomItem(TYPES);
  const res  = http.get(
    `${BASE_URL}/api/v1/properties/?city=${city}&type=${type}`,
    { tags: { endpoint: 'property_search' } },
  );
  const ok = check(res, { 'search 200': (r) => r.status === 200 });
  errorRate.add(!ok);
  searchLatency.add(res.timings.duration);
}

function filterByPrice() {
  const min = Math.floor(randomBetween(1_000_000, 10_000_000));
  const max = min + Math.floor(randomBetween(5_000_000, 50_000_000));
  const res = http.get(
    `${BASE_URL}/api/v1/properties/?min_price=${min}&max_price=${max}`,
    { tags: { endpoint: 'property_price_filter' } },
  );
  const ok = check(res, { 'price filter 200': (r) => r.status === 200 });
  errorRate.add(!ok);
}

function viewPropertyDetail() {
  if (!cachedPropertyId) {
    browseProperties();
    return;
  }
  const res = http.get(
    `${BASE_URL}/api/v1/properties/${cachedPropertyId}/`,
    { tags: { endpoint: 'property_detail' } },
  );
  const ok = check(res, { 'detail 200': (r) => r.status === 200 });
  errorRate.add(!ok);
}

function healthCheck() {
  const res = http.get(`${BASE_URL}/health/`, { tags: { endpoint: 'health' } });
  const ok  = check(res, {
    'health 200': (r) => r.status === 200,
    'health ok':  (r) => {
      try { return JSON.parse(r.body).status === 'ok'; } catch { return false; }
    },
  });
  errorRate.add(!ok);
}

function leadsDashboard() {
  if (!JWT_TOKEN) { browseProperties(); return; }
  const list  = http.get(`${BASE_URL}/api/v1/leads/`,       { headers: AUTH_HEADERS });
  const stats = http.get(`${BASE_URL}/api/v1/leads/stats/`, { headers: AUTH_HEADERS });
  const ok = check(list,  { 'leads list 200':  (r) => r.status === 200 })
          && check(stats, { 'leads stats 200': (r) => r.status === 200 });
  errorRate.add(!ok);
}

export default function () {
  const rand = Math.random();

  if      (rand < 0.25) browseProperties();
  else if (rand < 0.50) searchByCity();
  else if (rand < 0.65) filterByPrice();
  else if (rand < 0.80) viewPropertyDetail();
  else if (rand < 0.90) healthCheck();
  else                  leadsDashboard();

  sleep(randomBetween(1, 3));
}
