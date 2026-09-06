"""Job diario: precios -> deteccion de movimiento -> noticias -> explicacion.

Cada paso esta aislado: si una fuente falla, el resto del job sigue adelante.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .llm import explain_move
from .sources import news as news_src
from .sources import prices as prices_src
from .sources import topics as topics_src

log = logging.getLogger(__name__)

GOOD_STATUS = {"ok", "no_cause"}

MSG_NO_RELEVANTE = "Movimiento pequeno, dentro de la variacion normal del dia a dia."


def today_str() -> str:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = None
    return datetime.now(tz).strftime("%Y-%m-%d")


def _cache_metadata(quotes: dict[str, prices_src.Quote]) -> dict[str, dict[str, str | None]]:
    """Nombre y divisa vienen dentro de la respuesta de precios: coste cero."""
    meta = db.get_instruments()
    for ticker, quote in quotes.items():
        guardado = meta.get(ticker) or {}
        name = quote.name or guardado.get("name")
        currency = quote.currency or guardado.get("currency")
        if (name, currency) != (guardado.get("name"), guardado.get("currency")):
            try:
                db.upsert_instrument(ticker, name, currency)
            except Exception as exc:
                log.warning("no se pudo cachear metadatos de %s: %s", ticker, exc)
        meta[ticker] = {"name": name, "currency": currency}
    return meta


def run_daily_job(only: list[str] | None = None) -> dict:
    db.init_db()
    db.seed_portfolio(settings.tickers)
    run_id = db.start_job_run()
    day = today_str()
    # La cartera vive en la base de datos (editable desde la web); TICKERS del
    # .env solo sirve de semilla la primera vez.
    tickers = only or db.get_portfolio() or settings.tickers
    stats = {"day": day, "tickers": len(tickers), "con_precio": 0, "movimientos": 0,
             "explicados": 0, "errores": 0}

    if not tickers:
        db.finish_job_run(run_id, "error", "la cartera esta vacia")
        return stats

    try:
        quotes = prices_src.fetch_quotes(tickers)
    except Exception as exc:
        log.exception("fallo total obteniendo precios")
        db.finish_job_run(run_id, "error", f"precios: {exc}")
        return stats

    try:
        meta = _cache_metadata(quotes)
    except Exception as exc:
        log.warning("no se pudieron cargar metadatos: %s", exc)
        meta = {}

    def _name(ticker: str) -> str | None:
        # El nombre del .env manda: permite etiquetas propias y funciona
        # aunque Yahoo no devuelva metadatos.
        return settings.ticker_names.get(ticker) or (meta.get(ticker) or {}).get("name")

    def _currency(ticker: str) -> str | None:
        return (meta.get(ticker) or {}).get("currency")

    relevantes = []
    for ticker in tickers:
        quote = quotes.get(ticker)
        if quote is None:
            stats["errores"] += 1
            continue
        # Un fallo de las fuentes no debe borrar lo que ya funciono hoy.
        if not quote.ok and db.snapshot_has_price(ticker, day):
            log.warning("%s sin datos ahora; se conserva el snapshot previo de hoy", ticker)
            stats["errores"] += 1
            continue

        try:
            db.save_snapshot(
                ticker=ticker,
                day=day,
                price=quote.price,
                prev_close=quote.prev_close,
                change_pct=quote.change_pct,
                currency=quote.currency or _currency(ticker),
                source=quote.source,
                asof=quote.asof,
            )
        except Exception as exc:
            log.exception("no se pudo guardar el snapshot de %s: %s", ticker, exc)
            stats["errores"] += 1
            continue

        if not quote.ok:
            stats["errores"] += 1
            if not db.explanation_exists(ticker, day):
                db.save_explanation(
                    ticker, day,
                    "Sin datos de precio para este dia: la fuente no ha respondido.",
                    "unavailable", None, None,
                )
            continue

        stats["con_precio"] += 1
        if prices_src.is_relevant_move(quote):
            relevantes.append(quote)
        else:
            db.save_explanation(ticker, day, MSG_NO_RELEVANTE, "not_relevant", None, None)

    stats["movimientos"] = len(relevantes)

    if not relevantes:
        db.finish_job_run(run_id, "ok", f"sin movimientos relevantes ({stats})")
        return stats

    try:
        contexto = news_src.fetch_market_context()
    except Exception as exc:
        log.warning("sin contexto de mercado: %s", exc)
        contexto = []

    temas_manuales = db.get_portfolio_topics()

    for quote in relevantes:
        ticker = quote.ticker
        # Un ETF no tiene noticias propias: se buscan las del activo que replica.
        tema = topics_src.resolve_topic(_name(ticker), temas_manuales.get(ticker))
        try:
            titulares = news_src.fetch_news(
                ticker, _name(ticker), tema.query if tema else None
            )
        except Exception as exc:
            log.warning("noticias fallidas para %s: %s", ticker, exc)
            titulares = []

        try:
            db.save_news(ticker, day, titulares)
        except Exception as exc:
            log.warning("no se pudieron guardar noticias de %s: %s", ticker, exc)

        try:
            explicacion = explain_move(
                ticker=ticker,
                company=_name(ticker),
                change_pct=quote.change_pct or 0.0,
                price=quote.price,
                currency=quote.currency or _currency(ticker),
                headlines=titulares,
                context_headlines=contexto if not titulares else contexto[:2],
                topic_label=tema.label if tema else None,
            )
            previa = db.get_explanation_status(ticker, day)
            if explicacion.status == "unavailable" and previa in GOOD_STATUS:
                # Ya habia una explicacion valida hoy: no la degradamos.
                log.warning("%s: IA no disponible, se conserva la explicacion previa", ticker)
                stats["errores"] += 1
            else:
                db.save_explanation(
                    ticker, day, explicacion.summary, explicacion.status,
                    explicacion.provider, explicacion.model,
                )
            if explicacion.status in GOOD_STATUS:
                stats["explicados"] += 1
        except Exception as exc:
            log.exception("fallo generando explicacion de %s: %s", ticker, exc)
            stats["errores"] += 1
            if db.get_explanation_status(ticker, day) not in GOOD_STATUS:
                db.save_explanation(
                    ticker, day, "Sin explicacion disponible por un error interno.",
                    "unavailable", None, None,
                )
        time.sleep(1.0)  # ritmo suave para no tocar rate limits

    db.finish_job_run(run_id, "ok", str(stats))
    log.info("job terminado: %s", stats)
    return stats
