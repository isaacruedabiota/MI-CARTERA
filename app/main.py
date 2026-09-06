"""API + web. FastAPI sirviendo una unica pagina y unos pocos endpoints JSON."""
from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import db, jobs
from .config import settings
from .llm import provider_status
from .scheduler import start_scheduler, stop_scheduler
from .sources import prices, topics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_job_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.seed_portfolio(settings.tickers)
    if settings.scheduler_enabled:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Por que se mueve mi cartera", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _run_job_guarded(only: list[str] | None = None) -> None:
    if not _job_lock.acquire(blocking=False):
        log.info("ya hay un job en marcha, se ignora la peticion")
        return
    try:
        jobs.run_daily_job(only)
    except Exception:
        log.exception("el job diario ha fallado")
    finally:
        _job_lock.release()


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "threshold": settings.move_threshold_pct},
    )


@app.get("/api/days")
def api_days():
    return {"days": db.available_days()}


# --- gestion de la cartera --------------------------------------------------

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-^=]{0,19}$")


class TickerIn(BaseModel):
    ticker: str


class RenameIn(BaseModel):
    name: str | None = None
    topic: str | None = None


def _check_token(token: str | None) -> None:
    if settings.refresh_token and token != settings.refresh_token:
        raise HTTPException(status_code=401, detail="token invalido")


def _normalize(raw: str) -> str:
    ticker = (raw or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise HTTPException(status_code=400, detail="ticker con formato no valido")
    return ticker


def _label(ticker: str, alias: str | None) -> str:
    """Lo que se ve en grande. El alias sustituye al codigo del mercado."""
    return alias or ticker


def _official(ticker: str, oficial: str | None) -> str | None:
    return settings.ticker_names.get(ticker) or oficial


def _topic_info(ticker: str, nombre: str | None, manual: str | None) -> dict:
    tema = topics.resolve_topic(nombre, manual)
    return {
        "topic": tema.label if tema else None,
        "topic_query": tema.query if tema else None,
        "topic_manual": manual,
    }


def _decorate(tickers: list[str]) -> list[dict]:
    meta = db.get_instruments()
    alias = db.get_portfolio_aliases()
    temas = db.get_portfolio_topics()
    salida = []
    for t in tickers:
        nombre = _official(t, (meta.get(t) or {}).get("name"))
        salida.append({
            "ticker": t,
            "label": _label(t, alias.get(t)),
            "name": nombre,
            "alias": alias.get(t),
            "currency": (meta.get(t) or {}).get("currency"),
            **_topic_info(t, nombre, temas.get(t)),
        })
    return salida


@app.get("/api/tickers")
def api_tickers():
    return {"tickers": _decorate(db.get_portfolio())}


SEARCH_TTL = 120.0
SEARCH_CACHE_MAX = 100
_search_cache: dict[str, tuple[float, list[dict]]] = {}


def _search_with_prices(consulta: str) -> list[dict]:
    """Busca y anade la variacion del dia, descartando lo que no tenga precios."""
    ahora = time.monotonic()
    cacheado = _search_cache.get(consulta)
    if cacheado and ahora - cacheado[0] < SEARCH_TTL:
        return cacheado[1]

    # Se piden mas de los que se muestran porque una parte se caera al filtrar.
    candidatos = prices.search_symbols(consulta, limit=14)
    cambios = prices.fetch_change_batch([c["symbol"] for c in candidatos])

    resultados = []
    for item in candidatos:
        datos = cambios.get(item["symbol"])
        if not datos:
            continue  # sin serie de precios: no se puede seguir, no se ofrece
        resultados.append({**item, **datos})
        if len(resultados) >= 8:
            break

    # La divisa se pide aparte, solo para los que se van a mostrar: es lo que
    # distingue dos cotizaciones del mismo fondo (Amsterdam en USD, Paris en EUR).
    divisas = prices.fetch_currencies([r["symbol"] for r in resultados])
    for item in resultados:
        item["currency"] = divisas.get(item["symbol"])

    if len(_search_cache) >= SEARCH_CACHE_MAX:
        _search_cache.clear()
    _search_cache[consulta] = (ahora, resultados)
    return resultados


@app.get("/api/search")
def api_search(q: str = ""):
    """Busca por nombre, ticker o ISIN, con la variacion del ultimo dia."""
    consulta = (q or "").strip()
    if len(consulta) < 2:
        return {"query": consulta, "results": []}

    en_cartera = set(db.get_portfolio())
    resultados = [
        {**item, "in_portfolio": item["symbol"] in en_cartera}
        for item in _search_with_prices(consulta)
    ]
    return {"query": consulta, "results": resultados}


@app.post("/api/tickers", status_code=201)
def api_add_ticker(
    payload: TickerIn,
    background: BackgroundTasks,
    x_refresh_token: str | None = Header(default=None),
):
    _check_token(x_refresh_token)
    ticker = _normalize(payload.ticker)

    if ticker in db.get_portfolio():
        raise HTTPException(status_code=409, detail=f"{ticker} ya esta en la cartera")

    existe, nombre, divisa = prices.validate_ticker(ticker)
    if existe is False:
        raise HTTPException(
            status_code=404,
            detail=f"Yahoo Finance no da precios para {ticker}. Si lo has "
                   f"elegido en el buscador, prueba con otro mercado del mismo "
                   f"valor: algunas cotizaciones secundarias aparecen en la "
                   f"busqueda pero no tienen datos.",
        )

    db.add_ticker(ticker)
    if nombre or divisa:
        db.upsert_instrument(ticker, nombre, divisa)

    aviso = None
    if existe is None:
        aviso = ("No se ha podido verificar el ticker ahora mismo (la fuente de "
                 "precios no responde). Se ha anadido igualmente.")
    else:
        # Traemos ya su precio y explicacion para que no haya que esperar al job.
        background.add_task(_run_job_guarded, [ticker])

    return {
        "ticker": ticker,
        "name": settings.ticker_names.get(ticker) or nombre,
        "currency": divisa,
        "warning": aviso,
    }


@app.patch("/api/tickers/{ticker}")
def api_rename_ticker(
    ticker: str,
    payload: RenameIn,
    x_refresh_token: str | None = Header(default=None),
):
    """Cambia la etiqueta y/o el tema de noticias de un valor.

    Se actualiza solo lo que venga en el cuerpo: enviar `name` vacio devuelve el
    codigo de mercado, y `topic` vacio vuelve al tema deducido del nombre.
    """
    _check_token(x_refresh_token)
    normalizado = _normalize(ticker)
    if normalizado not in db.get_portfolio():
        raise HTTPException(status_code=404, detail=f"{normalizado} no esta en la cartera")

    alias = db.get_portfolio_aliases().get(normalizado)
    if payload.name is not None:
        alias = (payload.name or "").strip()[:30] or None
        db.set_alias(normalizado, alias)

    manual = db.get_portfolio_topics().get(normalizado)
    if payload.topic is not None:
        manual = (payload.topic or "").strip()[:60] or None
        db.set_topic(normalizado, manual)

    meta = db.get_instruments().get(normalizado) or {}
    nombre = _official(normalizado, meta.get("name"))
    return {
        "ticker": normalizado,
        "alias": alias,
        "label": _label(normalizado, alias),
        "name": nombre,
        **_topic_info(normalizado, nombre, manual),
    }


@app.delete("/api/tickers/{ticker}")
def api_remove_ticker(ticker: str, x_refresh_token: str | None = Header(default=None)):
    _check_token(x_refresh_token)
    normalizado = _normalize(ticker)
    if not db.remove_ticker(normalizado):
        raise HTTPException(status_code=404, detail=f"{normalizado} no esta en la cartera")
    # El historico se conserva: solo deja de consultarse a partir de ahora.
    return {"ticker": normalizado, "status": "eliminado"}


@app.get("/api/portfolio")
def api_portfolio(day: str | None = None):
    selected = day or db.latest_day()
    if not selected:
        return {
            "day": None,
            "items": [],
            "last_run": db.last_job_run(),
            "threshold": settings.move_threshold_pct,
        }
    # Solo se muestran los valores que estan hoy en la cartera. El historico de
    # los que se quitaron sigue en la base de datos, pero no se enseña.
    alias = db.get_portfolio_aliases()
    items = [i for i in db.portfolio_for_day(selected) if i["ticker"] in alias]
    for item in items:
        item["alias"] = alias.get(item["ticker"])
        item["label"] = _label(item["ticker"], item["alias"])
        item["name"] = _official(item["ticker"], item["name"])
    return {
        "day": selected,
        "items": items,
        "last_run": db.last_job_run(),
        "threshold": settings.move_threshold_pct,
    }


@app.get("/api/health")
def api_health():
    return {
        "status": "ok",
        "tickers": db.get_portfolio() or settings.tickers,
        "threshold_pct": settings.move_threshold_pct,
        "scheduler_enabled": settings.scheduler_enabled,
        "job_hour": f"{settings.job_hour:02d}:{settings.job_minute:02d}",
        "timezone": settings.timezone,
        "llm": provider_status(),
        "last_run": db.last_job_run(),
        "job_running": _job_lock.locked(),
    }


@app.post("/api/refresh")
def api_refresh(background: BackgroundTasks, x_refresh_token: str | None = Header(default=None)):
    _check_token(x_refresh_token)
    if _job_lock.locked():
        return JSONResponse({"status": "ya_en_marcha"}, status_code=409)
    background.add_task(_run_job_guarded, None)
    return {"status": "lanzado"}
