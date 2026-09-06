"""Adaptadores de proveedores de IA. Todos gratuitos y sin tarjeta.

Se usa httpx directamente en lugar de los SDK oficiales: menos dependencias que
compilar en ARM y una interfaz identica para los cuatro.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings
from .base import LLMError, LLMProvider

log = logging.getLogger(__name__)

TIMEOUT_CLOUD = 45.0
TIMEOUT_LOCAL = 300.0  # un modelo 3B en una Pi puede tardar minutos

# Los modelos con razonamiento (gpt-oss, qwen3...) gastan tokens pensando ANTES
# de escribir, y ese gasto sale del mismo presupuesto. Con 300 la respuesta se
# cortaba a media frase de vez en cuando. La respuesta util sigue siendo corta:
# esto es solo margen para el razonamiento.
MAX_TOKENS = 1200


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LLMError(f"error de red: {exc}") from exc
    if resp.status_code == 429:
        raise LLMError("limite de peticiones alcanzado (429)")
    if resp.status_code >= 400:
        raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise LLMError("respuesta no es JSON valido") from exc


class _OpenAICompatible(LLMProvider):
    """Groq y OpenRouter hablan el dialecto /chat/completions de OpenAI."""

    endpoint = ""
    api_key = ""
    extra_headers: dict[str, str] = {}

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError(f"falta la clave de {self.name}")
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": MAX_TOKENS,
        }
        data = _post_json(self.endpoint, payload, headers, TIMEOUT_CLOUD)
        try:
            choice = data["choices"][0]
            texto = choice["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise LLMError(f"respuesta inesperada de {self.name}: {str(data)[:200]}") from exc
        if choice.get("finish_reason") == "length":
            # Media frase es peor que no decir nada: se descarta.
            raise LLMError("respuesta truncada por limite de tokens")
        return texto


class GroqProvider(_OpenAICompatible):
    name = "groq"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self) -> None:
        self.api_key = settings.groq_api_key
        self.model = settings.groq_model


class OpenRouterProvider(_OpenAICompatible):
    name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self) -> None:
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model
        self.extra_headers = {"X-Title": "Por que se mueve mi cartera"}


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.host = settings.ollama_host
        self.model = settings.ollama_model

    def available(self) -> bool:
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=5)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 600},
        }
        data = _post_json(f"{self.host}/api/generate", payload, {}, TIMEOUT_LOCAL)
        text = (data.get("response") or "").strip()
        if not text:
            raise LLMError("ollama devolvio una respuesta vacia")
        return text


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError("falta GEMINI_API_KEY")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": MAX_TOKENS},
        }
        data = _post_json(url, payload, {"x-goog-api-key": self.api_key}, TIMEOUT_CLOUD)
        try:
            candidate = data["candidates"][0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                raise LLMError("respuesta truncada por limite de tokens")
            parts = candidate["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"respuesta inesperada de gemini: {str(data)[:200]}") from exc


_REGISTRY = {
    "groq": GroqProvider,
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
}


def get_provider(name: str) -> LLMProvider | None:
    factory = _REGISTRY.get((name or "").lower())
    if factory is None:
        if name and name != "none":
            log.warning("proveedor de IA desconocido: %s", name)
        return None
    return factory()
