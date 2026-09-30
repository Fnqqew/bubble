"""Pool de sesiones de Claude: arranca con una y abre más solo cuando hacen falta, ya que cada una es un proceso
claude.exe pesado.
"""

import asyncio

from bubble.config import ClaudeConfig
from bubble.translate import claude_subscription as cs

OPENED: list[int] = []


class FakeSession:
    count = 0

    def __init__(self, _options) -> None:
        FakeSession.count += 1
        self.number = FakeSession.count
        self.client = object()
        self.turns = 0

    async def open(self) -> None:
        await asyncio.sleep(0.05)
        OPENED.append(self.number)

    async def close(self) -> None:
        pass


def provider(monkeypatch, pool_size=3):
    monkeypatch.setattr(cs, "_Session", FakeSession)
    monkeypatch.setattr(cs, "find_claude_cli", lambda _path=None: "claude")
    FakeSession.count = 0
    OPENED.clear()
    return cs.ClaudeSubscriptionProvider(ClaudeConfig(pool_size=pool_size))


async def test_starts_with_a_single_session(monkeypatch):
    p = provider(monkeypatch)
    await p.start()
    assert len(p._all) == 1 and OPENED == [1]
    await p.close()


async def test_grows_only_when_all_are_busy_and_shrinks_later(monkeypatch):
    p = provider(monkeypatch)
    await p.start()
    busy = await p._idle.get()  # traducción en curso
    p._grow()  # llega otro pedido con todas ocupadas
    p._grow()
    await asyncio.sleep(0.15)
    assert len(p._all) == 3  # nunca más de pool_size
    p._grow()
    await asyncio.sleep(0.1)
    assert len(p._all) == 3
    p._idle.put_nowait(busy)
    await p._shrink()
    assert len(p._all) == 3  # hubo pedidos superpuestos: se mantienen
    p._last_crowded -= cs.SHRINK_AFTER_S + 1
    await p._shrink()
    assert len(p._all) == 1  # sin actividad reciente: queda una
    await p.close()
