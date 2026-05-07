"""
Ollama backend — runs local models via Ollama's OpenAI-compatible API.
Implements the tool-call agentic loop manually (no framework needed).

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

from .base import AIBackend, func_to_openai_schema

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6   # prevent infinite loops if model keeps calling tools


class OllamaBackend(AIBackend):

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=f"{settings.OLLAMA_BASE_URL}/v1",
                api_key='ollama',          # Ollama ignores this value
            )
        return self._client

    # ── AIBackend interface ───────────────────────────────────────────────────

    def chat(self, message: str, history: list,
             tools: list, system_prompt: str) -> str:
        client       = self._get_client()
        tool_schemas = [func_to_openai_schema(t) for t in tools]
        tool_map     = {t.__name__: t for t in tools}
        messages     = self._build_messages(system_prompt, history, message)

        for round_num in range(MAX_TOOL_ROUNDS):
            kwargs: dict = dict(
                model=settings.LOCAL_MODEL,
                messages=messages,
                temperature=0.3,
            )
            if tool_schemas:
                kwargs['tools'] = tool_schemas

            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:
                logger.error(f"Ollama chat failed (round {round_num}): {exc}")
                raise

            choice = response.choices[0]

            if choice.finish_reason == 'tool_calls' and choice.message.tool_calls:
                # Build assistant message (must include tool_calls field)
                messages.append({
                    'role':       'assistant',
                    'content':    choice.message.content or '',
                    'tool_calls': [
                        {
                            'id':   tc.id,
                            'type': 'function',
                            'function': {
                                'name':      tc.function.name,
                                'arguments': tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ],
                })

                # Execute each tool and append results
                for tc in choice.message.tool_calls:
                    result = self._run_tool(tc, tool_map)
                    messages.append({
                        'role':         'tool',
                        'tool_call_id': tc.id,
                        'content':      json.dumps(result, ensure_ascii=False),
                    })

            else:
                # Final text response
                return (choice.message.content or '').strip()

        logger.warning("Ollama: hit MAX_TOOL_ROUNDS without a final response")
        return "I couldn't complete that request. Please try rephrasing."

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
        return f"local:{settings.LOCAL_MODEL}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_messages(system_prompt: str, history: list, message: str) -> list:
        msgs = [{'role': 'system', 'content': system_prompt}]
        for item in history:
            role = 'assistant' if item.get('role') == 'model' else 'user'
            text = item.get('text', '').strip()
            if text:
                msgs.append({'role': role, 'content': text})
        msgs.append({'role': 'user', 'content': message})
        return msgs

    @staticmethod
    def _run_tool(tool_call, tool_map: dict) -> dict:
        name = tool_call.function.name
        fn   = tool_map.get(name)
        if fn is None:
            return {'error': f"Unknown tool: {name}"}
        try:
            raw_args = tool_call.function.arguments
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            return fn(**args)
        except Exception as exc:
            logger.error(f"Tool '{name}' raised: {exc}", exc_info=True)
            return {'error': str(exc)}
