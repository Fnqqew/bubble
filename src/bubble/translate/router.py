"""Elige proveedor: prueba en orden y pasa al siguiente si uno falla o tarda demasiado."""

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
        # Si en este tiempo no llegó nada, la sesión quedó colgada: se corta y se reintenta una vez (con otra
        # sesión) en vez de esperar el límite entero y mostrar un error. Normalmente la primera palabra llega en ~1,5 s.
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
        """Traduce varios mensajes en un pedido. `on_delta(índice, fragmento)` se llama mientras se generan."""
        errors: list[str] = []
        timeout = self.timeout_s + 1.0 * (len(requests) - 1)
        loop = asyncio.get_running_loop()
        for provider in self.providers:
            for attempt in (1, 2):
                chunks: list[list[str]] = [[] for _ in requests]
                ttfts: list[float | None] = [None] * len(requests)
                start = time.perf_counter()
                started = False
                try:
                    async with asyncio.timeout(min(self.first_token_s, timeout) if attempt == 1 else timeout) as limit:
                        async with aclosing(_batch_stream(provider, requests)) as stream:
                            async for index, chunk in stream:
                                if not started:  # ya responde: tiene el tiempo completo para terminar
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
                        # Ya se mostró texto parcial; no mezclar con otro intento ni con otro proveedor.
                        raise AllProvidersFailed("; ".join(errors)) from exc
                    if not stalled:
                        break  # un error de verdad (no una sesión colgada): siguiente proveedor
        raise AllProvidersFailed("; ".join(errors))


async def _batch_stream(provider: Provider, requests: list[TranslationRequest]):
    """Usa stream_batch si el proveedor lo tiene; si no, traduce de a uno."""
    if hasattr(provider, "stream_batch"):
        async with aclosing(provider.stream_batch(requests)) as stream:
            async for item in stream:
                yield item
        return
    for index, request in enumerate(requests):
        async with aclosing(provider.stream(request)) as stream:
            async for chunk in stream:
                yield index, chunk
