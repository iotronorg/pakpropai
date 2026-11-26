#!/usr/bin/env python3
"""
MVP criterion 1: discover → shortlist → enquire → book, on web, in the assistant and on
WhatsApp, with no staff action. Talks HTTP to a running server and checks outcomes, not
wording. Also checks criteria 2 (API and assistant give the same results), 3 (what is
listed is public), 4 (the agent has the enquiry within a minute and can answer) and
8 (nothing happens before the user confirms).

Local, everything included:   scripts/dev/e2e.sh
Staging (after scripts/e2e/seed.py there):
    E2E_BASE_URL=https://staging.example  E2E_WEB_TOKEN=…  E2E_ASSISTANT_TOKEN=…  E2E_WA_TOKEN=…  E2E_AGENT_TOKEN=…
    E2E_WA_PHONE=+92…  E2E_WA_APP_SECRET=…  E2E_WA_PHONE_ID=…  python scripts/e2e/consumer_journey.py
The tokens are access tokens of accounts you own; E2E_WA_TOKEN is for E2E_WA_PHONE.
Viewings made by the run are cancelled and its threads closed at the end.
"""
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid

import requests

BASE = os.environ.get('E2E_BASE_URL', 'http://localhost:8000').rstrip('/')
API = BASE + '/api/v1'
STUB = os.environ.get('E2E_STUB_URL', '').rstrip('/')          # local run: the OTP is read from here
PHONES = {'web': '+923000009001', 'assistant': '+923000009002', 'wa': os.environ.get('E2E_WA_PHONE', '+923000009003'),
          'agent': os.environ.get('E2E_AGENT_PHONE', '+923000009009')}
WA_SECRET, WA_PHONE_ID = os.environ.get('E2E_WA_APP_SECRET', ''), os.environ.get('E2E_WA_PHONE_ID', '')
WAIT = int(os.environ.get('E2E_WAIT_S', '60'))
CRITERIA = {'market': 'PK', 'purpose': 'sale', 'location': {'free_text': 'Lahore'}, 'keywords': 'e2ejourney'}
passed = 0


class Failed(Exception):
    pass


def check(ok, what, detail=''):
    global passed
    if not ok:
        raise Failed(f'{what}: {str(detail)[:500]}')
    passed += 1
    print(f'  [OK  ] {what}')


def until(fn, what, seconds=WAIT):
    """Poll for work done by the background worker."""
    start = time.monotonic()
    while True:
        value = fn()
        if value:
            check(True, f'{what} ({time.monotonic() - start:.0f}s)')
            return value
        if time.monotonic() - start > seconds:
            raise Failed(f'{what}: nothing after {seconds}s')
        time.sleep(1)


class Person:
    def __init__(self, name):
        self.name = name
        self.http = requests.Session()
        token = os.environ.get(f'E2E_{name.upper()}_TOKEN', '') or self.sign_in()
        self.http.cookies.clear()
        self.http.headers['Authorization'] = f'Bearer {token}'

    def sign_in(self):
        if not STUB:
            raise Failed(f'Set E2E_{self.name.upper()}_TOKEN')
        phone = PHONES[self.name]
        seen = len(requests.get(f'{STUB}/sent', timeout=10).json())
        r = self.http.post(f'{API}/auth/otp/send/', json={'phone': phone}, timeout=30)
        check(r.status_code == 200, f'{self.name}: one-time code requested', r.text)

        def code():
            for m in reversed(requests.get(f'{STUB}/sent', timeout=10).json()[seen:]):
                if m['to'] == phone.lstrip('+') and (found := re.search(r'\b(\d{6})\b', json.dumps(m.get('text') or m))):
                    return found[1]
        r = self.http.post(f'{API}/auth/otp/verify/', json={'phone': phone, 'code': until(code, f'{self.name}: code delivered')},
                           timeout=30)
        token = next((c.value for c in r.cookies if c.name == 'access_token'), '')
        check(r.status_code == 200 and token, f'{self.name}: signed in', r.text)
        return token

    def call(self, method, path, *, ok=(200, 201, 202), **kw):
        r = self.http.request(method, f'{API}/{path}', timeout=120, **kw)
        if r.status_code not in ok:
            raise Failed(f'{self.name}: {method} {path} → {r.status_code} {r.text[:500]}')
        return r.json() if r.content else None

    def rows(self, path, **kw):
        data = self.call('GET', path, **kw)
        return data['results'] if isinstance(data, dict) else data

    def threads(self):
        return [t for t in self.rows('threads/') if t['status'] == 'open']

    def viewings(self):
        return [v for v in self.rows('viewings/') if v['status'] not in ('cancelled', 'completed', 'no_show')]

    def tidy(self):
        for v in self.viewings():
            if str(v['title']).startswith('E2E House'):
                self.call('POST', f'viewings/{v["id"]}/cancel/')
        for t in self.threads():
            if t['subject'].startswith('E2E House'):
                self.call('POST', f'threads/{t["id"]}/close/')


def discover():
    print('Discover (anonymous)')
    r = requests.post(f'{API}/search/', json=CRITERIA, timeout=60)
    check(r.status_code == 200, 'search', r.text)
    ids = [h['listing']['id'] for h in r.json()['results']]
    check(len(ids) == 2, 'search finds the two seeded listings', ids)
    r = requests.get(f'{API}/public/listings/search/', params={'market': 'PK', 'purpose': 'sale', 'location': 'Lahore',
                                                               'q': 'e2ejourney'}, timeout=60)
    check(r.status_code == 200 and set(re.findall(r'[0-9a-f-]{36}', r.text)) >= set(ids), 'public search agrees', r.text)
    for i in ids:
        r = requests.get(f'{API}/public/listings/{i}/', timeout=60)
        check(r.status_code == 200 and r.json()['title'].startswith('E2E House'), 'public listing page', r.text)
    r = requests.get(f'{API}/public/listings/compare/', params={'ids': ','.join(ids)}, timeout=60)
    check(r.status_code == 200 and len(r.json()['items']) == 2, 'comparison', r.text)
    return ids


def free_slot(p, listing):
    slots = p.call('GET', 'viewing-slots/', params={'listing': listing, 'days': 7})['slots']
    check(slots, f'{p.name}: free viewing times')
    return slots[len(slots) // 2]


def web(p, agent, ids):
    print('Web: shortlist → enquire → book')
    p.call('POST', 'me/favourites/', json={'listing_id': ids[0]})
    check(ids[0] in [f['listing'] for f in p.rows('me/favourites/')], 'web: shortlisted')
    started, text = time.monotonic(), f'Is it available? {uuid.uuid4().hex[:6]}'
    t = p.call('POST', 'enquiries/', json={'listing_id': ids[0], 'message': text, 'contact_consent': True},
               headers={'Idempotency-Key': uuid.uuid4().hex})
    check(t['status'] == 'open', 'web: enquiry sent')
    until(lambda: any(x['id'] == t['id'] for x in agent.rows('threads/', params={'side': 'responder'})),
          'agent has the enquiry')
    until(lambda: any('enquiry' in n['title'].lower() for n in agent.rows('notifications/')), 'agent was notified')
    check(time.monotonic() - started < 60, 'agent has it within a minute')
    agent.call('POST', f'threads/{t["id"]}/messages/', json={'body': 'Yes, it is.'})
    check(p.rows(f'threads/{t["id"]}/messages/')[0]['body'] == 'Yes, it is.', 'web: the reply arrived')
    slot = free_slot(p, ids[0])
    v = p.call('POST', 'viewings/', json={'slot_id': slot['id'], 'contact_consent': True})
    check(v['listing'] == ids[0] and v['thread'] == t['id'], 'web: viewing booked', v)
    check(all(s['id'] != slot['id'] for s in p.call('GET', 'viewing-slots/', params={'listing': ids[0], 'days': 7})['slots']),
          'the time is no longer offered')


class Chat:
    """The assistant in the web app."""
    def __init__(self, p):
        self.p = p
        self.pk = p.call('POST', 'conversations/', json={})['id']

    def say(self, text):
        return self.p.call('POST', f'conversations/{self.pk}/messages/', json={
            'text': text, 'client_message_id': str(uuid.uuid4())})['message']


class WhatsApp:
    """Signed webhook in; answers are read from the conversation API."""
    def __init__(self, p):
        self.p, self.pk, self.seq = p, None, 0
        old = self._open()
        if old:
            self.pk = old['id']
            self.seq = max([m['seq'] for m in self._messages()] or [0])

    def _open(self):
        return next((c for c in self.p.rows('conversations/') if c['channel'] == 'whatsapp' and c['is_open']), None)

    def _messages(self):
        out, page = [], 1
        while True:
            data = self.p.call('GET', f'conversations/{self.pk}/messages/', params={'page': page})
            out += data['results']
            if not data.get('next'):
                return out
            page += 1

    def say(self, text):
        body = json.dumps({'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': WA_PHONE_ID},
            'messages': [{'id': f'wamid.e2e{uuid.uuid4().hex}', 'from': PHONES['wa'].lstrip('+'), 'type': 'text',
                          'text': {'body': text}}]}}]}]}).encode()
        sign = 'sha256=' + hmac.new(WA_SECRET.encode(), body, hashlib.sha256).hexdigest()
        r = requests.post(f'{API}/whatsapp/webhook/', data=body, timeout=60, headers={
            'Content-Type': 'application/json', 'X-Hub-Signature-256': sign})
        check(r.status_code == 200, f'whatsapp: "{text[:40]}" accepted', r.text)

        def reply():
            if self.pk is None:
                found = self._open()
                if not found:
                    return None
                self.pk = found['id']
            new = [m for m in self._messages() if m['seq'] > self.seq and m['role'] == 'assistant']
            return new[-1] if new else None
        m = until(reply, 'whatsapp: answered')
        self.seq = m['seq']
        if STUB:
            until(lambda: any(x['to'] == PHONES['wa'].lstrip('+') and m['content'].splitlines()[0][:40] in json.dumps(x, ensure_ascii=False)
                              for x in requests.get(f'{STUB}/sent', timeout=10).json()), 'whatsapp: answer delivered')
        return m


def assistant(label, p, agent, chat, ids):
    print(f'{label}: discover → shortlist → enquire → book → move → cancel')
    check(not p.threads() and not p.viewings(), f'{label}: starts with nothing')
    chat.say('I am looking for a house to buy in Lahore, keyword e2ejourney')
    shown = p.call('GET', f'conversations/{chat.pk}/state/')['last_results']
    check(list(map(str, shown)) == ids, f'{label}: same results as the search API', f'{shown} / {ids}')
    chat.say('Compare the first two listings')
    chat.say('Save the first one to my favourites')
    check(ids[0] in [f['listing'] for f in p.rows('me/favourites/')], f'{label}: shortlisted')

    m = chat.say('Please ask the lister of the first one: is it still available?')
    check(m['pending_action'] and not p.threads(), f'{label}: nothing is sent before a yes', m)
    chat.say('yes')
    threads = p.threads()
    check(len(threads) == 1 and threads[0]['listing'] == ids[0], f'{label}: enquiry sent', threads)
    until(lambda: any(x['id'] == threads[0]['id'] for x in agent.rows('threads/', params={'side': 'responder'})),
          'agent has the enquiry')

    chat.say('Show me the free times to visit the first one')
    start = free_slot(p, ids[0])['local_start']
    m = chat.say(f'Book a viewing of the first one at {start}')
    check(m['pending_action'] and not p.viewings(), f'{label}: nothing is booked before a yes', m)
    chat.say('yes')
    v = p.viewings()
    check(len(v) == 1 and v[0]['listing'] == ids[0] and v[0]['local_start'] == start, f'{label}: viewing booked', v)

    # KI-076: change or cancel the viewing in the chat, each only after a yes.
    chat.say('Which are my viewings?')
    later = next((x['local_start'] for x in p.call('GET', 'viewing-slots/', params={'listing': ids[0], 'days': 7})['slots']
                  if x['local_start'] != start), None)
    check(later, f'{label}: another free time')
    m = chat.say(f'Please move my viewing to {later}')
    check(m['pending_action'] and p.viewings()[0]['local_start'] == start, f'{label}: nothing is moved before a yes', m)
    chat.say('yes')
    v = p.viewings()
    check(len(v) == 1 and v[0]['local_start'] == later and v[0]['status'] == 'rescheduled', f'{label}: viewing moved', v)
    m = chat.say('Please cancel my viewing')
    check(m['pending_action'] and len(p.viewings()) == 1, f'{label}: nothing is cancelled before a yes', m)
    chat.say('yes')
    check(not p.viewings(), f'{label}: viewing cancelled', p.viewings())


def main():
    people = []
    try:
        agent = Person('agent')
        people = [Person('web'), Person('assistant')]
        if WA_SECRET:
            people.append(Person('wa'))
        for p in people:
            p.tidy()
        ids = discover()
        web(people[0], agent, ids)
        assistant('Assistant', people[1], agent, Chat(people[1]), ids)
        if WA_SECRET:
            assistant('WhatsApp', people[2], agent, WhatsApp(people[2]), ids)
        else:
            print('SKIPPED: WhatsApp (set E2E_WA_APP_SECRET, E2E_WA_PHONE_ID, E2E_WA_PHONE, E2E_WA_TOKEN)')
            return 2
    except Failed as exc:
        print(f'  [FAIL] {exc}')
        return 1
    finally:
        for p in people:
            try:
                p.tidy()
            except (Failed, requests.RequestException) as exc:
                print(f'  [WARN] could not tidy up: {exc}')
    print(f'PASSED: {passed} checks')
    return 0


if __name__ == '__main__':
    sys.exit(main())
