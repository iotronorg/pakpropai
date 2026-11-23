"""
Ollama backend — runs local models via Ollama's OpenAI-compatible API.
``step()`` returns tool calls; apps.ai.runtime runs the loop (T-2010).

Setup:
    brew install ollama
    ollama serve                    # starts local server on :11434
    ollama pull qwen2.5:7b          # primary chat + tool-use model
    ollama pull llava:7b            # optional — for image/document analysis

Env vars:
    AI_BACKEND=local
    LOCAL_MODEL=qwen2.5:7b
    LOCAL_VISION_MODEL=llava:7b     # optional; falls back to text if not pulled
    OLLAMA_BASE_URL=http://localhost:11434
"""
import json
import logging

from django.conf import settings

from apps.ai.usage import record_openai_response

from .base import AIBackend, Completion, Step, ToolCall

logger = logging.getLogger(__name__)


class OllamaBackend(AIBackend):
    name = 'local'

    def __init__(self):
        self._client = None

    # Subclasses (OpenAI-compatible) override these three.
    def _base_url(self) -> str:
        return f"{settings.OLLAMA_BASE_URL}/v1"

    def _api_key(self) -> str:
        return 'ollama'                    # Ollama ignores this value

    @property
    def _model(self) -> str:
        return settings.LOCAL_MODEL

    def is_configured(self) -> bool:
        return bool(settings.OLLAMA_BASE_URL)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self._base_url(), api_key=self._api_key(),
                                  timeout=settings.AI_TIMEOUT_S, max_retries=0)
        return self._client

    def complete(self, prompt, *, system='', max_tokens=512, temperature=0.2,
                 json_mode=False, timeout=30.0) -> Completion:
        messages = ([{'role': 'system', 'content': system}] if system else []) + \
                   [{'role': 'user', 'content': prompt}]
        kwargs = dict(model=self._model, messages=messages, max_tokens=max_tokens,
                      temperature=temperature, timeout=timeout)
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        response = self._get_client().chat.completions.create(**kwargs)
        record_openai_response(self._model, response)
        usage = getattr(response, 'usage', None)
        return Completion(text=(response.choices[0].message.content or '').strip(), model=self._model,
                          prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
                          completion_tokens=getattr(usage, 'completion_tokens', 0) or 0)

    # ── Explicit tool loop (T-2010) ───────────────────────────────────────────

    def step(self, system, messages, tools, *, max_tokens=1024, temperature=0.3, timeout=30.0) -> Step:
        kwargs = dict(model=self._model, messages=self._to_messages(system, messages),
                      max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        if tools:
            kwargs['tools'] = [{'type': 'function', 'function': t} for t in tools]
        response = self._get_client().chat.completions.create(**kwargs)
        record_openai_response(self._model, response)    # per step: every round is counted
        msg = response.choices[0].message
        calls = []
        for tc in msg.tool_calls or ():
            raw = tc.function.arguments
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = raw                               # the runtime reports it as invalid
            calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
        usage = getattr(response, 'usage', None)
        return Step(text=(msg.content or '').strip(), tool_calls=calls, model=self._model,
                    prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
                    completion_tokens=getattr(usage, 'completion_tokens', 0) or 0)

    @staticmethod
    def _to_messages(system, messages):
        out = [{'role': 'system', 'content': system}] if system else []
        for m in messages:
            if m['role'] == 'tool':
                out.append({'role': 'tool', 'tool_call_id': m['id'],
                            'content': json.dumps(m['result'], ensure_ascii=False, default=str)})
            elif m['role'] == 'assistant':
                item = {'role': 'assistant', 'content': m.get('text') or ''}
                if m.get('tool_calls'):
                    item['tool_calls'] = [
                        {'id': c.id, 'type': 'function',
                         'function': {'name': c.name, 'arguments': json.dumps(c.args, default=str)}}
                        for c in m['tool_calls']]
                out.append(item)
            elif m.get('text'):
                out.append({'role': 'user', 'content': m['text']})
        return out

    def analyze_image(self, image_bytes: bytes, mime_type: str,
                      prompt: str) -> str:
        """
        Uses LOCAL_VISION_MODEL (default: llava:7b) if available.
        Falls back gracefully if the vision model is not pulled.
        """
        vision_model = getattr(settings, 'LOCAL_VISION_MODEL', 'llava:7b')
        client = self._get_client()
        try:
            import base64
            b64 = base64.b64encode(image_bytes).decode()
            response = client.chat.completions.create(
                model=vision_model,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'text',       'text': prompt},
                        {'type': 'image_url',  'image_url': {'url': f"data:{mime_type};base64,{b64}"}},
                    ],
                }],
                max_tokens=800,
            )
            record_openai_response(vision_model, response)
            return (response.choices[0].message.content or '').strip()
        except Exception as exc:
            logger.warning(f"Ollama vision failed ({vision_model}): {exc}")
            return (
                "Image analysis requires a vision model.\n"
                f"Run: *ollama pull {vision_model}*\n\n"
                "For now, please describe the document in text and I'll help you."
            )

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        """
        Ollama does not support audio transcription.
        Returns a message asking the user to type instead.
        """
        return ''   # empty → router will ask user to type

    @property
    def label(self) -> str:
        return f"{self.name}:{self._model}"
