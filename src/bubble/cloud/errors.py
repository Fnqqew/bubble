"""Errores posibles con la nube. En todos los casos se continúa con el procesamiento local; si falta saldo o la clave es
inválida, el modo Pro se desactiva y se avisa al jugador.
"""

from __future__ import annotations


class CloudError(RuntimeError):
    """La nube no respondió correctamente; se continúa con el procesamiento local."""


class BadKey(CloudError):
    """Deepgram rechazó la clave."""


class NoCredit(CloudError):
    """La cuenta de Deepgram no tiene saldo."""


def error_for(status: int, detail: str = "") -> CloudError:
    if status in (401, 403):
        return BadKey("Deepgram no acepta tu clave")
    if status == 402:
        return NoCredit("Tu cuenta de Deepgram no tiene saldo")
    if status == 408:
        return CloudError("Deepgram tardó en responder")
    if status == 429:
        return CloudError("Deepgram está saturado: probá más tarde")
    if status >= 500:
        return CloudError(f"Deepgram tuvo un problema ({status})")
    return CloudError(f"Deepgram respondió {status} {readable(detail)}".strip())


def readable(detail: str, limit: int = 80) -> str:
    """Devuelve el detalle de un error, sin código de página (<html>…) y truncado a `limit` caracteres, para mostrarlo
    en la ventana.
    """
    import re

    text = " ".join(re.sub(r"<[^>]+>", " ", detail or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
