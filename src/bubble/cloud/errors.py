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
    return CloudError(f"Deepgram respondió {status} {detail}".strip())
