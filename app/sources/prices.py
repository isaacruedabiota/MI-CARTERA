"""Precios. Ninguna fuente requiere clave, cuenta ni tarjeta.

Cascada:
  1. Endpoint publico de graficos de Yahoo (httpx directo). Da cierres, divisa y
     a menudo el nombre del valor en una sola peticion por ticker.
  2. yfinance, si esta instalado. Util cuando Yahoo exige cookie+crumb: la
     libreria los gestiona sola. Es opcional a proposito: arrastra pandas y
     numpy, que en una Raspberry Pi pesan mucho.
  3. Stooq (CSV). Desde 2026 protege el endpoint con una verificacion
     JavaScript, asi que casi siempre fallara; se deja porque no cuesta nada y
     puede volver a funcionar.

Importante: NUNCA se usa yfinance .get_info() / .info. Es una llamada cara que
necesita cookie+crumb y falla con facilidad; el nombre y la divisa ya vienen en
la respuesta del endpoint de graficos, gratis.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..config import settings

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# OJO con el User-Agent: Yahoo responde 429 de forma sistematica a los UA de
# Linux ARM ("X11; Linux aarch64"), aunque las peticiones vayan muy espaciadas.
# No es un limite de frecuencia, es un filtro por cliente. Con un UA de escritorio
# responde 200 sin problema. No lo cambies a uno de ARM aunque corra en una Pi.
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://finance.yahoo.com/",
    "Origin": "https://finance.yahoo.com",
}
_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")

# Sufijo Yahoo -> sufijo Stooq. Mejor esfuerzo: la cobertura europea de Stooq
# es irregular; si falla, simplemente no hay tercer intento para ese ticker.
_STOOQ_SUFFIX = {
    "": ".us",
    ".MC": ".es",
    ".DE": ".de",
    ".F": ".de",
    ".PA": ".fr",
    ".AS": ".nl",
    ".BR": ".be",
    ".MI": ".it",
    ".L": ".uk",
    ".LS": ".pt",
    ".VI": ".at",
    ".SW": ".ch",
}


@dataclass
class Quote:
    ticker: str
    price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    name: str | None = None
    source: str = "none"
    asof: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None and self.change_pct is not None


def _pct(price: float, prev: float) -> float | None:
    if not prev:
        return None
    return round((price - prev) / prev * 100.0, 2)


# --- 1. Endpoint publico de graficos de Yahoo -------------------------------

def _parse_chart(payload: dict, ticker: str) -> Quote | None:
    try:
        result = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None

    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    try:
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        closes = []

    serie = [(s, c) for s, c in zip(stamps, closes) if c is not None]
    if len(serie) < 2:
        return None

    (_, prev), (stamp, price) = serie[-2], serie[-1]
    offset = timedelta(seconds=int(meta.get("gmtoffset") or 0))
    asof = (datetime.fromtimestamp(stamp, tz=timezone.utc) + offset).strftime("%Y-%m-%d")
    currency = meta.get("currency")

    return Quote(
        ticker=ticker,
        price=round(float(price), 4),
        prev_close=round(float(prev), 4),
        change_pct=_pct(float(price), float(prev)),
        currency=currency.upper() if currency else None,
        name=meta.get("longName") or meta.get("shortName"),
        source="yahoo-chart",
        asof=asof,
    )


def _fetch_yahoo_chart(ticker: str) -> Quote | None:
    params = {"range": "1mo", "interval": "1d"}
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{ticker}"
        try:
            resp = httpx.get(url, params=params, timeout=20, headers=_HEADERS)
            if resp.status_code == 429:
                log.warning("yahoo-chart: rate limit (429) en %s para %s", host, ticker)
                continue
            resp.raise_for_status()
            quote = _parse_chart(resp.json(), ticker)
            if quote and quote.ok:
                return quote
        except Exception as exc:
            log.warning("yahoo-chart fallido (%s, %s): %s", host, ticker, exc)
    return None


# --- 2. yfinance (opcional) -------------------------------------------------

def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401

        return True
    except Exception:
        return False


def _close_series(data, ticker: str):
    """Extrae la serie de cierres de un ticker del DataFrame de yf.download."""
    import pandas as pd

    sub = None
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in set(data.columns.get_level_values(0)):
            sub = data[ticker]
        elif ticker in set(data.columns.get_level_values(1)):
            sub = data.xs(ticker, axis=1, level=1)
    else:
        sub = data
    if sub is None:
        return None
    for col in ("Close", "Adj Close"):
        if col in sub.columns:
            series = sub[col].dropna()
            return series if not series.empty else None
    return None


def _fetch_yfinance(tickers: list[str]) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    if not tickers or not _yfinance_available():
        return quotes

    import yfinance as yf

    data = None
    for attempt in (1, 2):
        try:
            data = yf.download(
                tickers=tickers,
                period="1mo",
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                group_by="ticker",
            )
            if data is not None and not getattr(data, "empty", True):
                break
            log.warning("yfinance devolvio vacio (intento %d/2)", attempt)
            data = None
        except Exception as exc:
            log.warning("yfinance batch fallido (intento %d/2): %s", attempt, exc)
            data = None
        if attempt == 1:
            time.sleep(8)

    if data is None:
        return quotes

    for ticker in tickers:
        try:
            series = _close_series(data, ticker)
            if series is None or len(series) < 2:
                continue
            price, prev = float(series.iloc[-1]), float(series.iloc[-2])
            asof = series.index[-1]
            quotes[ticker] = Quote(
                ticker=ticker,
                price=round(price, 4),
                prev_close=round(prev, 4),
                change_pct=_pct(price, prev),
                source="yfinance",
                asof=asof.strftime("%Y-%m-%d") if hasattr(asof, "strftime") else str(asof)[:10],
            )
        except Exception as exc:
            log.warning("yfinance: no se pudo procesar %s: %s", ticker, exc)
    return quotes


# --- 3. Stooq ---------------------------------------------------------------

def _stooq_symbol(ticker: str) -> str | None:
    if "." in ticker:
        base, _, suffix = ticker.rpartition(".")
        mapped = _STOOQ_SUFFIX.get(f".{suffix}".upper())
        return f"{base.lower()}{mapped}" if mapped else None
    return f"{ticker.lower()}{_STOOQ_SUFFIX['']}"


def _fetch_stooq(ticker: str) -> Quote | None:
    symbol = _stooq_symbol(ticker)
    if not symbol:
        return None
    try:
        resp = httpx.get(
            f"https://stooq.com/q/d/l/?s={symbol}&i=d",
            timeout=20,
            follow_redirects=True,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        text = resp.text.strip()
        if not text.lower().startswith("date,"):
            log.warning("stooq no devolvio CSV para %s (verificacion anti-bot)", ticker)
            return None
        rows = list(csv.DictReader(io.StringIO(text)))
        closes = [
            (r["Date"], float(r["Close"]))
            for r in rows
            if r.get("Close") not in (None, "", "-", "N/D")
        ]
        if len(closes) < 2:
            return None
        (_, prev), (asof, price) = closes[-2], closes[-1]
        return Quote(
            ticker=ticker,
            price=round(price, 4),
            prev_close=round(prev, 4),
            change_pct=_pct(price, prev),
            source="stooq",
            asof=asof,
        )
    except Exception as exc:
        log.warning("stooq fallido para %s (%s): %s", ticker, symbol, exc)
        return None


# --- API publica ------------------------------------------------------------

def fetch_quotes(tickers: list[str]) -> dict[str, Quote]:
    """Cotizaciones de toda la cartera, con la cascada de fuentes completa."""
    quotes: dict[str, Quote] = {}

    for index, ticker in enumerate(tickers):
        quote = _fetch_yahoo_chart(ticker)
        if quote:
            quotes[ticker] = quote
        if index < len(tickers) - 1:
            time.sleep(1.5)  # ritmo suave: Yahoo limita por IP

    pendientes = [t for t in tickers if t not in quotes]
    if pendientes:
        log.info("fallback yfinance para %s", pendientes)
        for ticker, quote in _fetch_yfinance(pendientes).items():
            if quote.ok:
                quotes[ticker] = quote

    pendientes = [t for t in tickers if t not in quotes]
    for ticker in pendientes:
        quote = _fetch_stooq(ticker)
        if quote and quote.ok:
            quotes[ticker] = quote
        else:
            quotes[ticker] = Quote(
                ticker=ticker, error="sin datos de precio en ninguna fuente"
            )
        time.sleep(1.0)

    return quotes


def is_relevant_move(quote: Quote) -> bool:
    return quote.ok and abs(quote.change_pct or 0.0) >= settings.move_threshold_pct


# Tipos que tienen sentido en una cartera. Se descartan opciones, futuros y demas.
_SEARCHABLE_TYPES = {"EQUITY", "ETF", "MUTUALFUND", "INDEX"}


def search_symbols(query: str, limit: int = 8) -> list[dict]:
    """Busca valores por nombre, ticker o ISIN en el buscador publico de Yahoo.

    Sin clave ni cuenta. Devuelve lista vacia si la fuente no responde: el
    buscador es una comodidad, no debe romper nada.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    params = {"q": query, "quotesCount": limit * 2, "newsCount": 0, "listsCount": 0}
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v1/finance/search"
        try:
            resp = httpx.get(url, params=params, timeout=15, headers=_HEADERS)
            if resp.status_code >= 400:
                log.warning("busqueda: %s devolvio %s", host, resp.status_code)
                continue
            quotes = resp.json().get("quotes") or []
        except Exception as exc:
            log.warning("busqueda fallida en %s: %s", host, exc)
            continue

        resultados = []
        for item in quotes:
            symbol = (item.get("symbol") or "").strip()
            if not symbol or item.get("quoteType") not in _SEARCHABLE_TYPES:
                continue
            resultados.append(
                {
                    "symbol": symbol,
                    "name": item.get("longname") or item.get("shortname") or symbol,
                    "exchange": item.get("exchDisp") or item.get("exchange") or "",
                    "type": item.get("typeDisp") or "",
                }
            )
            if len(resultados) >= limit:
                break
        return resultados

    return []


def fetch_change_batch(symbols: list[str]) -> dict[str, dict]:
    """Variacion del ultimo dia de varios simbolos en UNA sola peticion.

    Usa el endpoint `spark`, que acepta una lista de simbolos (el de cotizaciones
    v7/quote pide autenticacion desde 2024 y devuelve 401). Solo se devuelven los
    simbolos con al menos dos cierres reales: el resto no tiene datos utilizables,
    aunque Yahoo les rellene otros campos.
    """
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}

    params = {"symbols": ",".join(symbols), "range": "5d", "interval": "1d"}
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/spark"
        try:
            resp = httpx.get(url, params=params, timeout=20, headers=_HEADERS)
            if resp.status_code >= 400:
                log.warning("spark: %s devolvio %s", host, resp.status_code)
                continue
            payload = resp.json()
        except Exception as exc:
            log.warning("spark fallido en %s: %s", host, exc)
            continue

        salida: dict[str, dict] = {}
        for symbol, data in (payload or {}).items():
            if not isinstance(data, dict):
                continue
            cierres = [c for c in (data.get("close") or []) if c is not None]
            if len(cierres) < 2:
                continue  # sin serie usable: no se puede anadir a la cartera
            price, prev = float(cierres[-1]), float(cierres[-2])
            salida[symbol] = {
                "price": round(price, 4),
                "prev_close": round(prev, 4),
                "change_pct": _pct(price, prev),
            }
        return salida

    return {}


def fetch_currencies(symbols: list[str], workers: int = 8) -> dict[str, str]:
    """Divisa de cotizacion de varios simbolos, en paralelo.

    No hay endpoint de Yahoo que devuelva la divisa para una lista de simbolos
    (v7/quote y v1/quoteType ya no estan disponibles sin autenticacion), asi que
    se pide el grafico de cada uno a la vez. Con 8 simbolos tarda ~0,7 s.

    Importa mas de lo que parece: un mismo fondo cotiza en USD en Amsterdam y en
    EUR en Paris, y sin la divisa no hay forma de saber cual es el que tienes.
    """
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}

    def una(symbol: str) -> tuple[str, str | None]:
        try:
            resp = httpx.get(
                f"https://{_YAHOO_HOSTS[0]}/v8/finance/chart/{symbol}",
                params={"range": "1d", "interval": "1d"},
                timeout=15,
                headers=_HEADERS,
            )
            if resp.status_code != 200:
                return (symbol, None)
            meta = resp.json()["chart"]["result"][0]["meta"]
            divisa = meta.get("currency")
            return (symbol, divisa.upper() if divisa else None)
        except Exception as exc:
            log.info("sin divisa para %s: %s", symbol, exc)
            return (symbol, None)

    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
        return {s: c for s, c in pool.map(una, symbols) if c}


def validate_ticker(ticker: str) -> tuple[bool | None, str | None, str | None]:
    """Comprueba que el ticker existe antes de anadirlo a la cartera.

    Devuelve (existe, nombre, divisa). `existe` es None cuando no se ha podido
    averiguar (Yahoo caido o limitando): en ese caso el ticker se acepta igual,
    avisando, para que un 429 no impida configurar la cartera.
    """
    params = {"range": "5d", "interval": "1d"}
    indeterminado = False

    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{ticker}"
        try:
            resp = httpx.get(url, params=params, timeout=15, headers=_HEADERS)
        except Exception as exc:
            log.warning("validacion de %s fallida en %s: %s", ticker, host, exc)
            indeterminado = True
            continue

        if resp.status_code == 429:
            indeterminado = True
            continue
        if resp.status_code == 404:
            return (False, None, None)  # Yahoo dice claramente que no existe
        if resp.status_code >= 400:
            indeterminado = True
            continue

        try:
            payload = resp.json()
        except ValueError:
            indeterminado = True
            continue

        quote = _parse_chart(payload, ticker)
        if quote:
            return (True, quote.name, quote.currency)
        # 200 limpio pero sin serie de precios: el simbolo no sirve. Le pasa por
        # ejemplo a "ITX" sin el sufijo .MC, que Yahoo resuelve a un cascaron
        # vacio. Un fallo real de la fuente llega como 429, 5xx o error de red.
        return (False, None, None)

    return (None if indeterminado else False, None, None)
