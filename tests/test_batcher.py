import asyncio

from bubble.capture.chat_parser import ChatTracker
from bubble.translate.base import ChatLine, TranslationRequest
from bubble.translate.batcher import Batcher
from bubble.translate.router import Router


class BatchProvider:
    """Proveedor simulado que registra los lotes recibidos y traduce pasando el texto a mayúsculas."""

    name = "fake"

    def __init__(self, delay: float = 0.05) -> None:
        self.batches: list[list[str]] = []
        self.delay = delay

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def stream_batch(self, requests):
        self.batches.append([r.text for r in requests])
        await asyncio.sleep(self.delay)
        for index, request in enumerate(requests):
            yield index, request.text.upper()


def req(text: str, target: str = "es") -> TranslationRequest:
    return TranslationRequest(text, target, "incoming")


async def test_burst_is_translated_in_one_batch_and_in_order():
    provider = BatchProvider()
    batcher = Batcher(Router([provider]), workers=2, gather_s=0.02)
    batcher.start()
    try:
        results = await asyncio.gather(*(batcher.translate(req(f"msg {i}")) for i in range(5)))
    finally:
        await batcher.close()
    assert [r.text for r in results] == [f"MSG {i}" for i in range(5)]
    assert provider.batches == [[f"msg {i}" for i in range(5)]]  # un único pedido para toda la ráfaga


async def test_batches_do_not_mix_different_targets_and_respect_max():
    provider = BatchProvider()
    batcher = Batcher(Router([provider]), workers=1, max_batch=2, gather_s=0.02)
    batcher.start()
    try:
        await asyncio.gather(
            batcher.translate(req("a")), batcher.translate(req("b", "en")),
            batcher.translate(req("c")), batcher.translate(req("d")),
        )
    finally:
        await batcher.close()
    assert provider.batches == [["a", "c"], ["b"], ["d"]]


async def test_deltas_go_to_the_right_message():
    provider = BatchProvider()
    batcher = Batcher(Router([provider]), gather_s=0.02)
    batcher.start()
    seen: dict[str, str] = {}
    try:
        await asyncio.gather(*(
            batcher.translate(req(t), on_delta=lambda chunk, t=t: seen.__setitem__(t, chunk)) for t in ("uno", "dos")
        ))
    finally:
        await batcher.close()
    assert seen == {"uno": "UNO", "dos": "DOS"}


def test_tracker_skips_old_messages_on_start_and_never_forgets_visible_ones():
    now = [0.0]
    tracker = ChatTracker(memory_s=10, clock=lambda: now[0], keep_on_start=2, confirm_frames=2)
    chat = [ChatLine(f"p{i}", f"mensaje viejo {i}") for i in range(5)]
    assert tracker.update(chat) == []
    assert tracker.update(chat) == chat[-2:]  # solo los 2 últimos al iniciar
    for second in range(1, 60):  # el mensaje sigue visible un minuto y nunca vuelve a salir como nuevo
        now[0] = second
        assert tracker.update(chat) == []


def test_scrolling_up_does_not_translate_history():
    tracker = ChatTracker(keep_on_start=0)
    tracker.update([ChatLine("a", "uno"), ChatLine("b", "dos"), ChatLine("c", "tres")])
    # El jugador sube en el chat: aparecen mensajes antiguos por encima de los conocidos.
    assert tracker.update([ChatLine("x", "viejo uno"), ChatLine("y", "viejo dos"), ChatLine("a", "uno")]) == []
    # Más arriba aún: no se ve ningún mensaje conocido, todo es historial.
    assert tracker.update([ChatLine("z", "muy viejo"), ChatLine("x", "viejo uno")]) == []
    # El jugador vuelve abajo y llega un mensaje nuevo: este sí se detecta.
    new = ChatLine("d", "cuatro")
    assert tracker.update([ChatLine("b", "dos"), ChatLine("c", "tres"), new]) == [new]
