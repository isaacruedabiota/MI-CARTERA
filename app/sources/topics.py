"""De que hablan realmente las noticias de un fondo.

Buscar noticias de "WisdomTree Physical Silver" no devuelve nada util: lo que
mueve a ese ETC es el precio de la plata, y las noticias hablan de la plata. Este
modulo traduce el nombre del fondo al tema que hay que buscar.

Solo se aplica a fondos e indices. Una accion normal ("Apple Inc.") no encaja en
ninguna regla y sigue buscandose por su propio nombre.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    query: str  # lo que se busca en Google News (en ingles: mas cobertura)
    label: str  # como se le explica al modelo y se muestra en la interfaz


# El orden importa: lo mas especifico primero. Cada entrada son las cadenas que
# deben aparecer en el nombre del fondo (en minusculas) para que aplique.
_RULES: list[tuple[tuple[str, ...], Topic]] = [
    # Materias primas
    (("physical silver", "silver etc"), Topic("silver price", "el precio de la plata")),
    (("physical gold", "physical swiss gold", "gold etc"),
     Topic("gold price", "el precio del oro")),
    (("physical platinum",), Topic("platinum price", "el precio del platino")),
    (("physical palladium",), Topic("palladium price", "el precio del paladio")),
    (("crude oil", "wti", "brent"), Topic("oil price", "el precio del petroleo")),
    (("bitcoin",), Topic("bitcoin price", "el precio del bitcoin")),

    # Indices de EE. UU.
    (("s&p 500", "s&p500", "sp 500"),
     Topic("S&P 500 stock market", "la bolsa estadounidense (S&P 500)")),
    (("nasdaq",), Topic("Nasdaq stock market", "el Nasdaq")),
    (("russell 2000",), Topic("Russell 2000 small caps", "las small caps de EE. UU.")),
    (("dow jones",), Topic("Dow Jones stock market", "el Dow Jones")),

    # Indices globales
    (("all-world", "all world", "acwi", "msci world", "world index"),
     Topic("global stock markets", "la bolsa mundial")),

    # Emergentes y Asia
    (("emerging markets", "msci em", "em imi", "emergentes"),
     Topic("emerging markets stocks", "las bolsas emergentes")),
    (("china", "csi 300", "hang seng"),
     Topic("Chinese stock market", "la bolsa china")),
    (("japan", "nikkei", "topix"),
     Topic("Japanese stock market", "la bolsa japonesa")),
    (("india", "nifty"), Topic("Indian stock market", "la bolsa india")),

    # Europa
    (("stoxx europe 600", "stoxx 600", "msci europe", "europe 600"),
     Topic("European stock markets", "la bolsa europea")),
    (("euro stoxx 50", "eurostoxx"),
     Topic("Euro Stoxx 50 stock market", "las grandes empresas de la zona euro")),
    (("ibex",), Topic("Ibex 35 bolsa espanola", "la bolsa espanola (Ibex 35)")),
    (("dax",), Topic("DAX German stock market", "la bolsa alemana (DAX)")),
    (("ftse 100",), Topic("FTSE 100 UK stock market", "la bolsa britanica")),

    # Renta fija
    (("treasury", "govt bond", "government bond", "aggregate bond", "bond index"),
     Topic("bond market yields", "el mercado de bonos")),
    (("high yield", "corporate bond"),
     Topic("corporate bond market", "los bonos corporativos")),

    # Sectores
    (("health care", "healthcare"),
     Topic("healthcare stocks", "el sector salud")),
    (("technology", "information technology"),
     Topic("technology stocks", "el sector tecnologico")),
    (("energy sector", "oil & gas"),
     Topic("energy sector stocks", "el sector energetico")),
]


def derive_topic(name: str | None) -> Topic | None:
    """Deduce el tema a partir del nombre oficial del fondo. None si no aplica."""
    if not name:
        return None
    limpio = name.lower()
    for patrones, topic in _RULES:
        if any(p in limpio for p in patrones):
            return topic
    return None


def resolve_topic(name: str | None, manual: str | None) -> Topic | None:
    """El tema escrito a mano manda sobre el deducido."""
    if manual:
        texto = manual.strip()
        if texto:
            return Topic(texto, texto)
    return derive_topic(name)
