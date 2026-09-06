"""Configuracion de la aplicacion. Todo sale de variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name) or default)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "si"}


@dataclass(frozen=True)
class Settings:
    tickers: list[str] = field(default_factory=list)
    ticker_names: dict[str, str] = field(default_factory=dict)
    move_threshold_pct: float = 2.5

    llm_provider: str = "groq"
    llm_fallback_provider: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    news_lang: str = "en"
    news_max_age_hours: int = 48
    news_max_items: int = 6

    db_path: Path = BASE_DIR / "data" / "portfolio.db"
    timezone: str = "Europe/Madrid"
    host: str = "0.0.0.0"
    port: int = 8000

    scheduler_enabled: bool = True
    job_hour: int = 22
    job_minute: int = 30
    refresh_token: str = ""


def _parse_tickers(raw: str) -> list[str]:
    seen: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        ticker = chunk.strip().upper()
        if ticker and ticker not in seen:
            seen.append(ticker)
    return seen


def _parse_names(raw: str) -> dict[str, str]:
    """Formato: AAPL=Apple Inc.;SAN.MC=Banco Santander"""
    names: dict[str, str] = {}
    for chunk in raw.split(";"):
        if "=" not in chunk:
            continue
        ticker, _, name = chunk.partition("=")
        ticker, name = ticker.strip().upper(), name.strip()
        if ticker and name:
            names[ticker] = name
    return names


def load_settings() -> Settings:
    db_raw = _get("DB_PATH", "data/portfolio.db")
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return Settings(
        tickers=_parse_tickers(_get("TICKERS", "AAPL,MSFT")),
        ticker_names=_parse_names(_get("TICKER_NAMES")),
        move_threshold_pct=abs(_get_float("MOVE_THRESHOLD_PCT", 2.5)),
        llm_provider=_get("LLM_PROVIDER", "groq").lower(),
        llm_fallback_provider=_get("LLM_FALLBACK_PROVIDER").lower(),
        groq_api_key=_get("GROQ_API_KEY"),
        groq_model=_get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        ollama_host=_get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=_get("OLLAMA_MODEL", "llama3.2:3b"),
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-2.0-flash"),
        openrouter_api_key=_get("OPENROUTER_API_KEY"),
        openrouter_model=_get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
        news_lang=_get("NEWS_LANG", "en").lower(),
        news_max_age_hours=_get_int("NEWS_MAX_AGE_HOURS", 48),
        news_max_items=_get_int("NEWS_MAX_ITEMS", 6),
        db_path=db_path,
        timezone=_get("TIMEZONE", "Europe/Madrid"),
        host=_get("HOST", "0.0.0.0"),
        port=_get_int("PORT", 8000),
        scheduler_enabled=_get_bool("SCHEDULER_ENABLED", True),
        job_hour=_get_int("JOB_HOUR", 22),
        job_minute=_get_int("JOB_MINUTE", 30),
        refresh_token=_get("REFRESH_TOKEN"),
    )


settings = load_settings()
