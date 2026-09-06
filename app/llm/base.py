"""Contrato comun de los proveedores de IA y construccion del prompt."""
from __future__ import annotations

from abc import ABC, abstractmethod

NO_CAUSE_MARKER = "[SIN_CAUSA_CLARA]"

SYSTEM_PROMPT = (
    "Eres un divulgador financiero que explica movimientos de bolsa a personas "
    "sin conocimientos tecnicos. Escribes SIEMPRE en espanol de Espana, claro y "
    "directo, sin jerga financiera y sin anglicismos innecesarios.\n"
    "Reglas estrictas:\n"
    "1. Responde con 2 o 3 frases como maximo. Nada de listas ni titulares.\n"
    "2. Basate UNICAMENTE en los titulares que te doy. No inventes cifras, "
    "fechas, resultados ni hechos que no aparezcan en ellos.\n"
    "3. Si los titulares no explican de forma razonable el movimiento, empieza "
    f"tu respuesta exactamente con {NO_CAUSE_MARKER} y di con naturalidad que no "
    "hay una causa concreta identificable y que probablemente se deba al "
    "movimiento general del mercado.\n"
    "4. No des recomendaciones de compra o venta, ni predicciones. No opines "
    "sobre si conviene invertir.\n"
    "5. No uses formato markdown, ni comillas, ni emojis."
)


class LLMError(RuntimeError):
    """Fallo recuperable de un proveedor: la app degrada, no rompe."""


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Devuelve texto plano o lanza LLMError."""

    def available(self) -> bool:
        return True


def build_prompt(
    ticker: str,
    company: str | None,
    change_pct: float,
    price: float | None,
    currency: str | None,
    headlines: list[dict],
    context_headlines: list[dict] | None = None,
    topic_label: str | None = None,
) -> str:
    direccion = "ha subido" if change_pct > 0 else "ha bajado"
    nombre = company or ticker
    precio = (
        f"Precio de cierre: {price} {currency or ''}".strip()
        if price is not None
        else "Precio de cierre: no disponible"
    )

    lineas = [
        f"Activo: {nombre} ({ticker})",
        f"Movimiento: {direccion} un {abs(change_pct):.2f}% respecto al cierre anterior.",
        precio,
        "",
    ]
    if topic_label:
        lineas.append(
            f"Este activo es un fondo que sigue {topic_label}. No tiene noticias "
            f"propias: lo que lo mueve es {topic_label}, asi que los titulares de "
            f"abajo van sobre eso. Explicalo en esos terminos, sin hablar del "
            f"fondo como si fuera una empresa."
        )
        lineas.append("")
        lineas.append(f"Titulares recientes sobre {topic_label}:")
    else:
        lineas.append("Titulares recientes sobre este activo:")
    if headlines:
        for item in headlines:
            fuente = item.get("publisher") or "fuente desconocida"
            lineas.append(f"- {item['title']} ({fuente})")
    else:
        lineas.append("- (no se ha encontrado ninguna noticia especifica de este activo)")

    if context_headlines:
        lineas.append("")
        lineas.append("Titulares generales de mercado del mismo periodo:")
        for item in context_headlines:
            fuente = item.get("publisher") or "fuente desconocida"
            lineas.append(f"- {item['title']} ({fuente})")

    lineas.append("")
    lineas.append(
        "Explica en 2 o 3 frases, en lenguaje sencillo, por que se ha movido asi. "
        "Si los titulares no lo justifican, aplica la regla 3."
    )
    return "\n".join(lineas)
