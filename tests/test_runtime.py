"""
T-2010 — explicit tool-loop runtime and tool registry (ADR-029).

Scripted fake providers drive the loop: tool execution by the runtime (never
the SDK), argument validation and coercion, unknown/disabled tools, tool
exceptions, terminal tools returned verbatim, the step limit, per-step usage,
audit records, failover rules (read-only tools may be re-run, committing ones
may not). The Gemini and OpenAI-compatible ``step()`` adapters are tested
against SDK-shaped fakes (message conversion both ways).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.ai import llm, runtime
from apps.ai import tool_registry as R
from apps.ai.backends.base import AIBackend, Step, ToolCall

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


class Scripted(AIBackend):
    """Returns the scripted steps in order; records what it was sent."""
    def __init__(self, name, steps, configured=True):
        self.name, self.steps, self.configured, self.sent = name, list(steps), configured, []

    def is_configured(self):
        return self.configured

    def step(self, system, messages, tools, **kw):
        self.sent.append([dict(m) for m in messages])
        nxt = self.steps.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def complete(self, *a, **k):
        raise NotImplementedError

    def analyze_image(self, *a):
        return ''

    def transcribe_audio(self, *a):
        return ''

    @property
    def label(self):
        return f'{self.name}:m'


def final(text, pin=5, pout=3):
    return Step(text, [], 'm', pin, pout)


def calls(*items):
    return Step('', [ToolCall(f'c{i}', n, a) for i, (n, a) in enumerate(items)], 'm', 7, 1)


def spec(name, fn, **kw):
    return R.ToolSpec(name, func=fn, **kw)


class RuntimeLoopTest(TestCase):
    def test_runtime_executes_tools_and_counts_every_step(self):
        seen = []

        def search(city: str, max_price: int = 0) -> dict:
            """Search."""
            seen.append((city, max_price))
            return {'results': [{'id': 1}]}

        b = Scripted('gemini', [calls(('search', {'city': 'Lahore', 'max_price': '2,500,000'})), final('Found 1')])
        turn = runtime.run_turn(b, 'flats in lahore', [{'role': 'model', 'text': 'hi'}], [spec('search', search)], 'sys')
        self.assertEqual(seen, [('Lahore', 2_500_000)])                 # coerced from the model's string
        self.assertEqual((turn.text, turn.steps, turn.prompt_tokens, turn.completion_tokens), ('Found 1', 2, 12, 4))
        self.assertEqual(turn.tool_calls[0]['status'], 'ok')
        second = b.sent[1]
        self.assertEqual([m['role'] for m in second], ['assistant', 'user', 'assistant', 'tool'])
        self.assertEqual(second[-1]['result'], {'results': [{'id': 1}]})

    def test_invalid_unknown_and_raising_tools_become_results(self):
        def boom(x: int) -> dict:
            """Boom."""
            raise RuntimeError('db down')

        b = Scripted('gemini', [
            calls(('boom', {}), ('boom', {'x': 'abc'}), ('nope', {}), ('boom', {'x': 1, 'extra': 2})),
            final('sorry')])
        turn = runtime.run_turn(b, 'x', [], [spec('boom', boom)], 'sys')
        self.assertEqual([c['status'] for c in turn.tool_calls], ['rejected', 'rejected', 'rejected', 'error'])
        results = [m['result']['error'] for m in b.sent[1] if m['role'] == 'tool']
        self.assertIn('missing required: x', results[0])
        self.assertIn('x: expected integer', results[1])
        self.assertIn('Unknown or unavailable tool', results[2])
        self.assertNotIn('db down', results[3])                          # internal error not leaked
        self.assertEqual(turn.text, 'sorry')

    def test_terminal_tool_reply_is_verbatim_and_contacts_redacted_in_audit(self):
        def connect(city: str, phone: str = '') -> dict:
            """Connect."""
            return {'found': True, 'whatsapp_summary': 'Agent: Ali +92300 1234567'}

        b = Scripted('local', [calls(('connect', {'city': 'Lahore', 'phone': '+923001112222'}))])
        turn = runtime.run_turn(b, 'agent please', [], [spec('connect', connect, terminal=True)], 'sys')
        self.assertEqual((turn.text, turn.terminal_tool, turn.steps), ('Agent: Ali +92300 1234567', 'connect', 1))
        self.assertEqual(turn.tool_calls[0]['args']['phone'], '***')

    @override_settings(AI_MAX_TOOL_STEPS=2)
    def test_step_limit(self):
        def ping() -> dict:
            """Ping."""
            return {}
        b = Scripted('gemini', [calls(('ping', {})), calls(('ping', {}))])
        turn = runtime.run_turn(b, 'x', [], [spec('ping', ping)], 'sys')
        self.assertEqual((turn.text, turn.steps), (runtime.STEP_LIMIT_REPLY, 2))


@override_settings(CACHES=LOCMEM, AI_PROVIDER_ORDER=['gemini', 'openai'])
class FailoverTest(TestCase):
    def setUp(self):
        cache.clear()

    def _use(self, **backends):
        p = patch('apps.ai.llm.make_backend', side_effect=lambda n: backends.get(n, Scripted(n, [], configured=False)))
        p.start()
        self.addCleanup(p.stop)

    def test_read_only_tool_may_rerun_on_next_provider(self):
        ran = []

        def search(city: str) -> dict:
            """Search."""
            ran.append(city)
            return {'n': 0}

        g = Scripted('gemini', [calls(('search', {'city': 'A'})), ConnectionError('down')])
        o = Scripted('openai', [calls(('search', {'city': 'A'})), final('none found')])
        self._use(gemini=g, openai=o)
        r = llm.chat('x', [], [spec('search', search)], 'sys', log=False)
        self.assertEqual((r.provider, r.text, ran), ('openai', 'none found', ['A', 'A']))

    def test_committing_tool_blocks_failover(self):
        made = []

        def create(city: str) -> dict:
            """Create."""
            made.append(city)
            return {'success': True}

        g = Scripted('gemini', [calls(('create', {'city': 'A'})), ConnectionError('down')])
        o = Scripted('openai', [final('should not run')])
        self._use(gemini=g, openai=o)
        with self.assertRaises(runtime.TurnCommitted):
            llm.chat('x', [], [spec('create', create, side_effect=R.LOW)], 'sys', log=False)
        self.assertEqual((made, o.sent), (['A'], []))

    def test_failed_committing_tool_does_not_commit(self):
        def create(city: str) -> dict:
            """Create."""
            return {'error': 'needs confirmation'}
        g = Scripted('gemini', [calls(('create', {'city': 'A'})), ConnectionError('down')])
        o = Scripted('openai', [final('ok')])
        self._use(gemini=g, openai=o)
        self.assertEqual(llm.chat('x', [], [spec('create', create, side_effect=R.LOW)], 'sys',
                                  log=False).provider, 'openai')


class RegistryTest(TestCase):
    def test_registry_covers_agent_tools_with_policies(self):
        from apps.ai import tools as tool_module
        for name, s in R.REGISTRY.items():
            self.assertTrue(callable(getattr(tool_module, name)), name)
            self.assertEqual(s.schema['name'], name)
        self.assertEqual(R.REGISTRY['initiate_deal_lock'].side_effect, R.FINANCIAL)
        self.assertTrue(R.REGISTRY['connect_to_agent'].terminal and R.REGISTRY['connect_to_agent'].commits)
        self.assertFalse(R.REGISTRY['search_properties'].commits)

    def test_enabled_tools_follow_flags(self):
        names = {s.name for s in R.enabled_tools({'feature_deal_lock': False, 'feature_scam_check': False})}
        self.assertNotIn('initiate_deal_lock', names)
        self.assertNotIn('run_fraud_check', names)
        self.assertIn('search_properties', names)
        self.assertNotIn('initiate_deal_lock', {s.name for s in R.enabled_tools({})})   # default off

    def test_patched_tool_module_is_used(self):
        with patch('apps.ai.tools.run_fraud_check', return_value={'risk': 'low'}) as m:
            self.assertEqual(R.REGISTRY['run_fraud_check'].fn(), {'risk': 'low'})
        m.assert_called_once()


class GeminiStepTest(TestCase):
    def test_disables_afc_and_round_trips_function_calls(self):
        from google.genai import types

        from apps.ai.backends.gemini import GeminiBackend
        part = types.Part(function_call=types.FunctionCall(id='f1', name='search', args={'city': 'Lahore'}))
        response = types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role='model', parts=[part]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=9, candidates_token_count=2))
        client = MagicMock()
        client.models.generate_content.return_value = response
        b = GeminiBackend()
        with patch.object(GeminiBackend, '_client_instance', return_value=client), \
                patch('apps.ai.backends.gemini.record_gemini_response') as rec:
            step = b.step('sys', [{'role': 'user', 'text': 'hi'}],
                          [{'name': 'search', 'description': 'S', 'parameters': {'type': 'object', 'properties': {}}}])
        rec.assert_called_once()
        self.assertEqual((step.tool_calls[0].name, step.tool_calls[0].args, step.prompt_tokens), ('search', {'city': 'Lahore'}, 9))
        cfg = client.models.generate_content.call_args.kwargs['config']
        self.assertTrue(cfg.automatic_function_calling.disable)
        self.assertEqual(cfg.tools[0].function_declarations[0].name, 'search')
        contents = GeminiBackend._to_contents_v2([
            {'role': 'user', 'text': 'hi'},
            {'role': 'assistant', 'text': '', 'tool_calls': [ToolCall('f1', 'search', {'city': 'L'})]},
            {'role': 'tool', 'id': 'f1', 'name': 'search', 'result': [1, 2]},
            {'role': 'tool', 'id': 'f2', 'name': 'search', 'result': {'ok': 1}},
        ])
        self.assertEqual([c.role for c in contents], ['user', 'model', 'user'])
        self.assertEqual(len(contents[2].parts), 2)                     # both results in one turn
        self.assertEqual(contents[2].parts[0].function_response.response, {'result': [1, 2]})


class OpenAIStepTest(TestCase):
    def test_tool_calls_parsed_and_messages_converted(self):
        from apps.ai.backends.ollama import OllamaBackend
        tc = SimpleNamespace(id='t1', function=SimpleNamespace(name='search', arguments='{"city": "Dubai"}'))
        bad = SimpleNamespace(id='t2', function=SimpleNamespace(name='search', arguments='{oops'))
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc, bad]))],
                               usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1))
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        b = OllamaBackend()
        with patch.object(OllamaBackend, '_get_client', return_value=client), \
                patch('apps.ai.backends.ollama.record_openai_response'):
            step = b.step('sys', [{'role': 'user', 'text': 'q'}],
                          [{'name': 'search', 'description': '', 'parameters': {}}])
        self.assertEqual([c.args for c in step.tool_calls], [{'city': 'Dubai'}, '{oops'])
        sent = client.chat.completions.create.call_args.kwargs
        self.assertEqual(sent['tools'][0]['type'], 'function')
        msgs = OllamaBackend._to_messages('sys', [
            {'role': 'assistant', 'text': '', 'tool_calls': [ToolCall('t1', 'search', {'city': 'D'})]},
            {'role': 'tool', 'id': 't1', 'name': 'search', 'result': {'n': 0}}])
        self.assertEqual([m['role'] for m in msgs], ['system', 'assistant', 'tool'])
        self.assertEqual(msgs[1]['tool_calls'][0]['function']['arguments'], '{"city": "D"}')
        with self.assertRaises(R.ToolArgsInvalid):                      # unparsable args → rejected
            R.validate_args(R.REGISTRY['search_properties'], '{oops')
