"""Lo que puede salir mal con la nube. Siempre se sigue con lo de tu PC; sin saldo o con la clave mala, el Pro se
apaga solo y avisa."""

from __future__ import annotations


class CloudError(RuntimeError):
    """La nube no respondió bien: se sigue con tu PC."""


class BadKey(CloudError):
    """Deepgram no acepta la clave."""


class NoCredit(CloudError):
    """La cuenta de Deepgram se quedó sin saldo."""


def error_for(status: int, detail: str = "") -> CloudError:
    if status in (401, 403):
        return BadKey("Deepgram no acepta tu clave")
    if status == 402:
        return NoCredit("Tu cuenta de Deepgram no tiene saldo")
    if status == 408:
        return CloudError("Deepgram tardó en responder")
    if status == 429:
        return CloudError("Deepgram está saturado: probá en un rato")
    if status >= 500:
        return CloudError(f"Deepgram tuvo un problema ({status})")
    return CloudError(f"Deepgram respondió {status} {readable(detail)}".strip())


def readable(detail: str, limit: int = 80) -> str:
    """El detalle de un error, sin código de página (<html>…) y cortito, para mostrarlo en la ventana."""
    import re

    text = " ".join(re.sub(r"<[^>]+>", " ", detail or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
