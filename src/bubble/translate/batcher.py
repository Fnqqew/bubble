"""Agrupa los mensajes que llegan casi juntos y los traduce en un solo pedido.

En un chat activo llegan ráfagas de mensajes. Traducirlos de a uno hace que los últimos esperen su turno varios segundos
y que las traducciones lleguen desordenadas. En lote salen juntas, en orden, más rápido y con menor costo, porque el
prompt fijo se lee una sola vez por lote.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .base import DeltaCallback, TranslationRequest
from .router import RoutedTranslation, Router


@dataclass
class _Item:
    request: TranslationRequest
    on_delta: DeltaCallback | None
    future: asyncio.Future = field(repr=False)


def _group_key(request: TranslationRequest) -> tuple:
    # Solo se agrupan mensajes que se traducen igual: misma dirección, idioma, variante y tono.
    return (request.direction, request.target_lang, request.target_region, request.tone, request.spoken,
            request.from_speech, request.intonation, request.examples, request.vocabulary)


class Batcher:
    def __init__(self, router: Router, workers: int = 3, max_batch: int = 6, gather_s: float = 0.06) -> None:
        self.router = router
        self.workers = max(1, workers)
        self.max_batch = max_batch
        self.gather_s = gather_s
        self._pending: list[_Item] = []
        self._wakeup = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if not self._tasks:
            loop = asyncio.get_running_loop()
            self._tasks = [loop.create_task(self._worker()) for _ in range(self.workers)]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        for item in self._pending:
            if not item.future.done():
                item.future.cancel()
        self._pending.clear()

    async def translate(self, request: TranslationRequest, on_delta: DeltaCallback | None = None) -> RoutedTranslation:
        if not self._tasks:
            return await self.router.translate(request, on_delta)
        item = _Item(request, on_delta, asyncio.get_running_loop().create_future())
        self._pending.append(item)
        self._wakeup.set()
        return await item.future

    def _take_batch(self) -> list[_Item]:
        if not self._pending:
            self._wakeup.clear()
            return []
        key = _group_key(self._pending[0].request)
        batch = [item for item in self._pending if _group_key(item.request) == key][: self.max_batch]
        taken = {id(item) for item in batch}
        self._pending = [item for item in self._pending if id(item) not in taken]
        if not self._pending:
            self._wakeup.clear()
        return batch

    async def _worker(self) -> None:
        while True:
            await self._wakeup.wait()
            await asyncio.sleep(self.gather_s)  # espera breve para agrupar mensajes que llegan juntos
            batch = self._take_batch()
            if not batch:
                continue

            def on_delta(index: int, chunk: str, batch=batch) -> None:
                callback = batch[index].on_delta
                if callback:
                    callback(chunk)

            try:
                results = await self.router.translate_batch([item.request for item in batch], on_delta)
            except Exception as exc:  # noqa: BLE001 - se informa a cada mensaje del lote
                for item in batch:
                    if not item.future.done():
                        item.future.set_exception(exc)
                continue
            for item, result in zip(batch, results):
                if not item.future.done():
                    item.future.set_result(result)
