from __future__ import annotations

import asyncio

from bubble.translate.base import TranslationRequest
from bubble.translate.langdetect import Detection


class FakeProvider:
    def __init__(self, name: str = "fake", reply: str = "hola", fail: bool = False, delay: float = 0.0) -> None:
        self.name = name
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.requests: list[TranslationRequest] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def stream(self, request: TranslationRequest):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.name} caído")
        for word in self.reply.split(" "):
            yield word + " "


class FakeDetector:
    """Detecta según un diccionario fijo texto -> (idioma, confianza)."""

    def __init__(self, table: dict[str, tuple[str, float]] | None = None) -> None:
        self.table = table or {}

    def load(self) -> None:
        pass

    def detect(self, text: str) -> Detection | None:
        if text in self.table:
            return Detection(*self.table[text])
        return None
