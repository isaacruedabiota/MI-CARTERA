"""Generacion de la explicacion. Si nada funciona, degrada con un mensaje claro."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import settings
from .base import NO_CAUSE_MARKER, SYSTEM_PROMPT, LLMError, build_prompt
from .providers import get_provider

log = logging.getLogger(__name__)

MSG_SIN_NOTICIAS = (
    "No se han encontrado noticias recientes sobre este activo, asi que no hay una "
    "causa concreta identificable. Lo mas probable es que se trate del movimiento "
    "general del mercado."
)
MSG_SIN_IA = (
    "Sin explicacion disponible: el modelo de IA no ha respondido. Los titulares "
    "recogidos ese dia siguen visibles debajo."
)


@dataclass
class Explanation:
    summary: str
    status: str  # ok | no_cause | no_news | unavailable
    provider: str | None = None
    model: str | None = None


def _clean(text: str) -> str:
    text = text.replace("**", "").replace("*", "").replace("#", "").strip()
    text = " ".join(text.split())
    return text[:800]


def _providers_to_try() -> list:
    names = [settings.llm_provider]
    if settings.llm_fallback_provider and settings.llm_fallback_provider not in names:
        names.append(settings.llm_fallback_provider)
    providers = []
    for name in names:
        provider = get_provider(name)
        if provider is not None:
            providers.append(provider)
    return providers


def explain_move(
    ticker: str,
    company: str | None,
    change_pct: float,
    price: float | None,
    currency: str | None,
    headlines: list[dict],
    context_headlines: list[dict] | None = None,
    topic_label: str | None = None,
) -> Explanation:
    if not headlines and not context_headlines:
        return Explanation(MSG_SIN_NOTICIAS, "no_news")

    prompt = build_prompt(
        ticker, company, change_pct, price, currency, headlines,
        context_headlines, topic_label,
    )

    for provider in _providers_to_try():
        try:
            if not provider.available():
                log.warning("proveedor %s no disponible, se salta", provider.name)
                continue
            raw = provider.complete(SYSTEM_PROMPT, prompt)
            text = _clean(raw)
            if not text:
                raise LLMError("respuesta vacia")
            status = "ok"
            if text.startswith(NO_CAUSE_MARKER) or NO_CAUSE_MARKER in text:
                text = _clean(text.replace(NO_CAUSE_MARKER, ""))
                status = "no_cause"
            return Explanation(text, status, provider.name, provider.model)
        except LLMError as exc:
            log.warning("proveedor %s fallido: %s", provider.name, exc)
        except Exception as exc:  # nunca dejamos que rompa el job
            log.exception("error inesperado en proveedor %s: %s", provider.name, exc)

    return Explanation(MSG_SIN_IA, "unavailable")


def provider_status() -> dict:
    """Estado del proveedor configurado, para /api/health."""
    provider = get_provider(settings.llm_provider)
    if provider is None:
        return {"provider": settings.llm_provider or "none", "available": False, "model": None}
    try:
        available = provider.available()
    except Exception:
        available = False
    return {"provider": provider.name, "available": available, "model": provider.model}
