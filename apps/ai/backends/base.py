"""
Abstract AI backend + utility to convert typed Python functions to OpenAI-format tool schemas.
Every backend (Gemini, Ollama) implements AIBackend.
"""
import inspect
import re
from abc import ABC, abstractmethod
from typing import get_type_hints

# Python type → JSON Schema type
_TYPE_MAP = {
    str:   'string',
    int:   'integer',
    float: 'number',
    bool:  'boolean',
}


def func_to_openai_schema(func) -> dict:
    """
    Convert a typed Python function with a Google-style docstring into an
    OpenAI-format tool schema dict.  Used by the Ollama backend.
    """
    try:
        sig   = inspect.signature(func)
        hints = get_type_hints(func)
        doc   = inspect.getdoc(func) or ''
    except Exception:
        return {'type': 'function', 'function': {'name': func.__name__,
                'description': '', 'parameters': {'type': 'object', 'properties': {}}}}

    description = doc.split('\n')[0].strip()

    # Parse "Args:" section (Google-style docstring)
    param_docs: dict[str, str] = {}
    in_args = False
    for line in doc.split('\n'):
        stripped = line.strip()
        if stripped == 'Args:':
            in_args = True
            continue
        if in_args:
            # Exit args block on any non-indented header
            if stripped and not line.startswith('    ') and not line.startswith('\t'):
                in_args = False
                continue
            m = re.match(r'(\w+):\s+(.*)', stripped)
            if m:
                param_docs[m.group(1)] = m.group(2)

    properties: dict = {}
    required:   list = []

    for name, param in sig.parameters.items():
        json_type = _TYPE_MAP.get(hints.get(name), 'string')
        prop: dict = {'type': json_type}
        if name in param_docs:
            prop['description'] = param_docs[name]
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        'type': 'function',
        'function': {
            'name':        func.__name__,
            'description': description,
            'parameters':  {
                'type':       'object',
                'properties': properties,
                'required':   required,
            },
        },
    }


class AIBackend(ABC):
    """
    Common interface for all AI backends.
    history format:  [{'role': 'user'|'model', 'text': str}, ...]
    tools:           list of plain Python callables (typed + docstrings)
    """

    @abstractmethod
    def chat(self, message: str, history: list,
             tools: list, system_prompt: str) -> str:
        """Single conversational turn. Returns reply text."""

    @abstractmethod
    def analyze_image(self, image_bytes: bytes, mime_type: str,
                      prompt: str) -> str:
        """Analyze an image (OCR, document check, photo). Returns text."""

    @abstractmethod
    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        """Transcribe a voice message. Returns plain text."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Human-readable label for logging (e.g. 'gemini:gemini-2.5-flash-lite')."""
