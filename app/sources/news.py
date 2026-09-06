"""Noticias via RSS publicos. Sin claves, sin cuentas, sin tarjeta.

Solo se leen titulares y el resumen que el propio feed publica: nunca se
descarga el cuerpo del articulo. Es mas ligero para la Pi y evita paywalls.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import httpx

from ..config import settings

log = logging.getLogger(__name__)

# Mismo motivo que en prices.py: nada de User-Agent de Linux ARM.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Feeds de contexto general de mercado, para cuando no hay noticia del valor.
MARKET_FEEDS = [
    ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Expansion Mercados", "https://e00-expansion.uecdn.es/rss/mercados.xml"),
]

# Sufijos de mercado que hay que quitar antes de buscar en Google News.
_MARKET_SUFFIXES = (
    ".MC", ".DE", ".F", ".PA", ".AS", ".BR", ".MI", ".L", ".LS", ".VI", ".SW",
)


def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=settings.news_max_age_hours)


def _entry_datetime(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    except Exception:
        return None


def _fetch_feed(url: str) -> list:
    """Descarga con httpx (control de timeout y UA) y parsea con feedparser."""
    import feedparser

    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": _UA})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return list(parsed.entries or [])
    except Exception as exc:
        log.warning("feed fallido %s: %s", url, exc)
        return []


def _clean_ticker(ticker: str) -> str:
    upper = ticker.upper()
    for suffix in _MARKET_SUFFIXES:
        if upper.endswith(suffix):
            return upper[: -len(suffix)]
    return upper


def _google_news_url(query: str) -> str:
    if settings.news_lang == "es":
        locale = "hl=es&gl=ES&ceid=ES:es"
    else:
        locale = "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale}"


def _google_news_items(query: str) -> list[dict]:
    """Titulares de una consulta cualquiera de Google News."""
    items = []
    cutoff = _cutoff()
    for entry in _fetch_feed(_google_news_url(query)):
        published = _entry_datetime(entry)
        if published and published < cutoff:
            continue
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        publisher = None
        source = getattr(entry, "source", None)
        if source is not None:
            publisher = getattr(source, "title", None)
        # Google News titula "Headline - Medio": quitamos el sufijo para que no
        # cuente como noticia distinta de la misma que ya trae Yahoo.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        items.append(
            {
                "title": title,
                "publisher": publisher or "Google News",
                "url": getattr(entry, "link", None),
                "published_at": published.isoformat() if published else None,
                "source_feed": "google_news",
            }
        )
    return items


def _from_google_news(ticker: str, company: str | None) -> list[dict]:
    """Busqueda por el nombre del valor. Entre comillas: queremos ese valor."""
    subject = company or _clean_ticker(ticker)
    query = f'"{subject}" stock' if settings.news_lang != "es" else f'"{subject}" bolsa'
    return _google_news_items(query)


def _from_yahoo(ticker: str) -> list[dict]:
    """Noticias del ticker via yfinance. Opcional: si no esta, se salta."""
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:
        log.warning("yahoo news fallido para %s: %s", ticker, exc)
        return []

    items: list[dict] = []
    cutoff = _cutoff()
    for entry in raw:
        try:
            # yfinance ha cambiado el formato varias veces: soportamos ambos.
            content = entry.get("content") if isinstance(entry, dict) else None
            if content:
                title = (content.get("title") or "").strip()
                provider = (content.get("provider") or {}).get("displayName")
                url = (content.get("canonicalUrl") or {}).get("url")
                raw_date = content.get("pubDate") or content.get("displayTime")
                published = None
                if raw_date:
                    published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            else:
                title = (entry.get("title") or "").strip()
                provider = entry.get("publisher")
                url = entry.get("link")
                stamp = entry.get("providerPublishTime")
                published = (
                    datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp else None
                )
            if not title:
                continue
            if published:
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    continue
            items.append(
                {
                    "title": title,
                    "publisher": provider or "Yahoo Finance",
                    "url": url,
                    "published_at": published.isoformat() if published else None,
                    "source_feed": "yahoo",
                }
            )
        except Exception as exc:
            log.debug("entrada de yahoo news ignorada: %s", exc)
    return items


def _norm(title: str) -> str:
    return "".join(ch for ch in title.lower() if ch.isalnum())[:80]


def _dedupe(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = _norm(item["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def fetch_news(
    ticker: str, company: str | None = None, topic: str | None = None
) -> list[dict]:
    """Noticias recientes de un valor.

    Si es un fondo, `topic` trae el tema que de verdad lo mueve (la plata, la
    bolsa europea...) y se busca eso: preguntar por el nombre del ETF no
    devuelve nada util. Para acciones normales, cascada Yahoo -> Google News.
    """
    if topic:
        # Sin comillas: aqui interesa el tema en general, no una frase exacta.
        items = _google_news_items(topic)
    else:
        items = _from_yahoo(ticker)
        if len(items) < settings.news_max_items:
            items += _from_google_news(ticker, company)
    items.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    return _dedupe(items, settings.news_max_items)


def fetch_market_context(limit: int = 5) -> list[dict]:
    """Titulares generales de mercado, para movimientos sin noticia propia."""
    items: list[dict] = []
    cutoff = _cutoff()
    for name, url in MARKET_FEEDS:
        for entry in _fetch_feed(url)[:10]:
            published = _entry_datetime(entry)
            if published and published < cutoff:
                continue
            title = getattr(entry, "title", "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "publisher": name,
                    "url": getattr(entry, "link", None),
                    "published_at": published.isoformat() if published else None,
                    "source_feed": "market_context",
                }
            )
    items.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    return _dedupe(items, limit)
