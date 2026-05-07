import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai"

    def ready(self):
        import os
        if os.environ.get('RUN_MAIN') != 'true':
            return  # skip in the StatReloader parent process — only print once
        from django.conf import settings
        backend = getattr(settings, 'AI_BACKEND', 'gemini').strip().lower()
        if backend == 'local':
            model = getattr(settings, 'LOCAL_MODEL', 'qwen2.5:7b')
            base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            print(f"\033[96m[AI] Backend: LOCAL (Ollama) | model={model} | url={base_url}\033[0m")
        else:
            model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite')
            key_set = bool(getattr(settings, 'GEMINI_API_KEY', ''))
            status = "\033[92mSET\033[0m" if key_set else "\033[91mMISSING ⚠\033[0m"
            print(f"\033[96m[AI] Backend: GEMINI | model={model} | api_key={status}\033[0m")
