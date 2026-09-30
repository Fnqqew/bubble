"""Elige un proveedor: los prueba en orden y pasa al siguiente si uno falla o tarda demasiado."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing
from dataclasses import dataclass
from typing import Callable

from .base import DeltaCallback, Provider, TranslationRequest

log = logging.getLogger(__name__)
BatchDeltaCallback = Callable[[int, str], None]
# El proveedor avisa con (SENT, "") cuando el pedido salió efectivamente, es decir, cuando ya obtuvo una sesión libre.
# Hasta ese momento el pedido puede estar esperando turno, y esa espera no equivale a una sesión bloqueada: si contara,
# un pedido en cola detrás de otro se cortaría por "sin respuesta" y reiniciaría la única sesión disponible.
SENT = -1
QUEUE_WAIT_S = 20.0  # tope para obtener una sesión libre (normalmente menos de 2 s)


@dataclass
class RoutedTranslation:
    text: str
    provider: str
    ttft_s: float | None


class AllProvidersFailed(RuntimeError):
    pass


class Router:
    def __init__(self, providers: list[Provider], timeout_s: float = 8.0, first_token_s: float = 5.0) -> None:
        if not providers:
            raise ValueError("Se necesita al menos un proveedor de traducción")
        self.providers = providers
        self.timeout_s = timeout_s
        # Si en este plazo no llega nada, se considera la sesión bloqueada: se corta y se reintenta una vez con otra
        # sesión, en lugar de esperar el límite completo y mostrar un error. Normalmente la primera palabra llega en
        # ~1,5 s.
        self.first_token_s = first_token_s

    async def start(self) -> None:
        await asyncio.gather(*(p.start() for p in self.providers))

    async def close(self) -> None:
        await asyncio.gather(*(p.close() for p in self.providers), return_exceptions=True)

    async def translate(self, request: TranslationRequest, on_delta: DeltaCallback | None = None) -> RoutedTranslation:
        callback = (lambda _index, chunk: on_delta(chunk)) if on_delta else None
        return (await self.translate_batch([request], callback))[0]

    async def translate_batch(
        self, requests: list[TranslationRequest], on_delta: BatchDeltaCallback | None = None
    ) -> list[RoutedTranslation]:
        """Traduce varios mensajes en un solo pedido. `on_delta(índice, fragmento)` se invoca a medida que se
        genera el texto.
        """
        errors: list[str] = []
        timeout = self.timeout_s + 1.0 * (len(requests) - 1)
        loop = asyncio.get_running_loop()
        for provider in self.providers:
            for attempt in (1, 2):
                chunks: list[list[str]] = [[] for _ in requests]
                ttfts: list[float | None] = [None] * len(requests)
                start = time.perf_counter()
                started = False
                first_limit = min(self.first_token_s, timeout) if attempt == 1 else timeout
                try:
                    # Sin aviso de envío (otros proveedores), el plazo corre desde el inicio.
                    wait = QUEUE_WAIT_S if getattr(provider, "reports_sent", False) else first_limit
                    async with asyncio.timeout(wait) as limit:
                        async with aclosing(_batch_stream(provider, requests)) as stream:
                            async for index, chunk in stream:
                                if index == SENT:  # ya tiene sesión: desde aquí corre el tiempo de respuesta
                                    start = time.perf_counter()
                                    limit.reschedule(loop.time() + first_limit)
                                    continue
                                if not started:  # ya responde: dispone del tiempo completo para terminar
                                    started = True
                                    limit.reschedule(loop.time() + timeout - (time.perf_counter() - start))
                                if ttfts[index] is None:
                                    ttfts[index] = time.perf_counter() - start
                                chunks[index].append(chunk)
                                if on_delta:
                                    on_delta(index, chunk)
                    return [
                        RoutedTranslation("".join(parts).strip(), provider.name, ttft)
                        for parts, ttft in zip(chunks, ttfts)
                    ]
                except Exception as exc:  # noqa: BLE001 - se reintenta o se prueba el siguiente proveedor
                    stalled = isinstance(exc, TimeoutError) and not started
                    reason = ("sin respuesta" if stalled else "timeout") if isinstance(exc, TimeoutError) else (
                        str(exc) or type(exc).__name__)
                    log.warning("Proveedor %s falló (intento %d): %s", provider.name, attempt, reason)
                    errors.append(f"{provider.name}: {reason}")
                    if any(chunks):
                        # Ya se mostró texto parcial: no se mezcla con otro intento ni con otro proveedor.
                        raise AllProvidersFailed("; ".join(errors)) from exc
                    if not stalled:
                        break  # error real (no una sesión bloqueada): pasa al siguiente proveedor
        raise AllProvidersFailed("; ".join(errors))


async def _batch_stream(provider: Provider, requests: list[TranslationRequest]):
    """Usa stream_batch si el proveedor lo implementa; si no, traduce de a un mensaje."""
    if hasattr(provider, "stream_batch"):
        async with aclosing(provider.stream_batch(requests)) as stream:
            async for item in stream:
                yield item
        return
    for index, request in enumerate(requests):
        async with aclosing(provider.stream(request)) as stream:
            async for chunk in stream:
                yield index, chunk
