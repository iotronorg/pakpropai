"""
Gemini backend (google.genai). Since T-2010 the SDK never runs tools:
``step()`` disables Automatic Function Calling and returns the model's
function calls to ``apps.ai.runtime``, which validates and executes them.
"""
import logging

from django.conf import settings

from apps.ai.usage import record_gemini_response

from .base import AIBackend, Completion, Step, ToolCall

logger = logging.getLogger(__name__)


def _api_key() -> str:
    from apps.config.services import SystemConfigService
    return SystemConfigService.get('gemini_api_key') or settings.GEMINI_API_KEY


class GeminiBackend(AIBackend):
    name = 'gemini'

    def __init__(self):
        self._client = None
        self._api_key = None

    def is_configured(self) -> bool:
        return bool(_api_key())

    def _client_instance(self):
        key = _api_key()
        if self._client is None or key != self._api_key:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=int(settings.AI_TIMEOUT_S * 1000)))
            self._api_key = key
        return self._client

    def complete(self, prompt, *, system='', max_tokens=512, temperature=0.2,
                 json_mode=False, timeout=30.0) -> Completion:
        from google.genai import types
        cfg = types.GenerateContentConfig(max_output_tokens=max_tokens, temperature=temperature,
                                          system_instruction=system or None,
                                          http_options=types.HttpOptions(timeout=int(timeout * 1000)))
        if json_mode:
            cfg.response_mime_type = 'application/json'
        model = settings.GEMINI_MODEL
        response = self._client_instance().models.generate_content(model=model, contents=prompt, config=cfg)
        record_gemini_response(model, response)
        meta = getattr(response, 'usage_metadata', None)
        return Completion(text=(response.text or '').strip(), model=model,
                          prompt_tokens=getattr(meta, 'prompt_token_count', 0) or 0,
                          completion_tokens=getattr(meta, 'candidates_token_count', 0) or 0)

    # ── Explicit tool loop (T-2010) ───────────────────────────────────────────

    def step(self, system, messages, tools, *, max_tokens=1024, temperature=0.3, timeout=30.0) -> Step:
        from google.genai import types
        cfg = types.GenerateContentConfig(
            system_instruction=system or None, temperature=temperature, max_output_tokens=max_tokens,
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            cfg.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t['name'], description=t['description'],
                                          parameters_json_schema=t['parameters']) for t in tools])]
        model = settings.GEMINI_MODEL
        response = self._client_instance().models.generate_content(
            model=model, contents=self._to_contents_v2(messages), config=cfg)
        record_gemini_response(model, response)          # per step: every round is counted
        content = response.candidates[0].content if response.candidates else None
        parts = list(getattr(content, 'parts', None) or [])
        text = ''.join(p.text for p in parts if getattr(p, 'text', None) and not getattr(p, 'thought', False))
        calls = [ToolCall(id=fc.id or f'call_{n}', name=fc.name, args=dict(fc.args or {}))
                 for n, fc in enumerate(p.function_call for p in parts if getattr(p, 'function_call', None))]
        meta = getattr(response, 'usage_metadata', None)
        return Step(text=text.strip(), tool_calls=calls, model=model, raw=content,
                    prompt_tokens=getattr(meta, 'prompt_token_count', 0) or 0,
                    completion_tokens=getattr(meta, 'candidates_token_count', 0) or 0)

    @staticmethod
    def _to_contents_v2(messages):
        from google.genai import types
        out, pending = [], []

        def flush():
            if pending:
                out.append(types.Content(role='user', parts=list(pending)))
                pending.clear()
        for m in messages:
            if m['role'] == 'tool':
                result = m['result'] if isinstance(m['result'], dict) else {'result': m['result']}
                pending.append(types.Part(function_response=types.FunctionResponse(
                    id=m['id'], name=m['name'], response=result)))
                continue
            flush()
            if m['role'] == 'assistant':
                if m.get('raw') is not None and m.get('provider') == 'gemini':
                    out.append(m['raw'])                  # keeps thought signatures intact
                    continue
                parts = [types.Part(text=m['text'])] if m.get('text') else []
                parts += [types.Part(function_call=types.FunctionCall(id=c.id, name=c.name, args=c.args))
                          for c in m.get('tool_calls') or ()]
                if parts:
                    out.append(types.Content(role='model', parts=parts))
            elif m.get('text'):
                out.append(types.Content(role='user', parts=[types.Part(text=m['text'])]))
        flush()
        return out

    def analyze_image(self, image_bytes: bytes, mime_type: str,
                      prompt: str) -> str:
        from google import genai
        from google.genai import types
        client = self._client_instance()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_text(prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=800, temperature=0.1,
            ),
        )
        record_gemini_response(settings.GEMINI_MODEL, response)
        return (response.text or '').strip()

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        from google import genai
        from google.genai import types
        client = self._client_instance()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_text(
                    "Transcribe this voice message exactly as spoken. "
                    "Output ONLY the transcription."
                ),
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=512, temperature=0.1,
            ),
        )
        record_gemini_response(settings.GEMINI_MODEL, response)
        return (response.text or '').strip()

    @property
    def label(self) -> str:
        return f"gemini:{settings.GEMINI_MODEL}"
