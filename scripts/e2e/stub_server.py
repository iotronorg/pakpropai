#!/usr/bin/env python3
"""
Stands in for the two outside services of a local end-to-end run:

* ``/v1/chat/completions``: a model that picks a tool from words in the user's message;
* ``/wa/<phone id>/messages``: WhatsApp's API. ``GET /sent`` lists what was sent.
"""
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENT = []


def decide(text: str):
    t = text.lower()
    if m := re.search(r'\bbook\b.*?(\d{4}-\d\d-\d\dt\d\d:\d\d)', t):
        return 'book_viewing', {'listing': '1', 'start': m[1].upper()}
    if 'free times' in t:
        return 'find_viewing_slots', {'listing': '1'}
    if 'compare' in t:
        return 'compare_listings', {'listings': '1,2'}
    if 'save' in t:
        return 'save_listing', {'listing': '1'}
    if 'ask the lister' in t:
        return 'create_enquiry', {'listing': '1', 'message': 'Is it still available?'}
    if 'looking for' in t:
        return 'search_listings', {'location': 'Lahore', 'purpose': 'buy', 'keywords': 'e2ejourney'}
    return None


def answer(body: dict) -> dict:
    last = body['messages'][-1]
    tools = {t['function']['name'] for t in body.get('tools') or ()}
    if last['role'] == 'user' and tools and (choice := decide(str(last['content']))) and choice[0] in tools:
        return {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': uuid.uuid4().hex, 'type': 'function',
            'function': {'name': choice[0], 'arguments': json.dumps(choice[1])}}]}
    return {'role': 'assistant', 'content': 'Here is what I found.' if last['role'] == 'tool' else 'How can I help?'}


class Handler(BaseHTTPRequestHandler):
    def reply(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(SENT if self.path == '/sent' else {'ok': True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        if self.path.endswith('/chat/completions'):
            self.reply({'id': uuid.uuid4().hex, 'object': 'chat.completion', 'created': 0, 'model': 'stub',
                        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': answer(body)}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
        else:
            if body.get('type') in ('text', 'template'):
                SENT.append(body)
            self.reply({'messages': [{'id': f'wamid.{uuid.uuid4().hex}'}]})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 9000), Handler).serve_forever()
