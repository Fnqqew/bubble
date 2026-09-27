import asyncio

import pytest

from bubble.translate.base import TranslationRequest
from bubble.translate.router import AllProvidersFailed, Router
from fakes import FakeProvider

REQUEST = TranslationRequest("hello", "es", "incoming")


async def test_streams_deltas_and_reports_provider():
    deltas: list[str] = []
    routed = await Router([FakeProvider(reply="hola amigo")]).translate(REQUEST, deltas.append)
    assert routed.text == "hola amigo"
    assert routed.provider == "fake"
    assert "".join(deltas).strip() == "hola amigo"
    assert routed.ttft_s is not None


async def test_falls_back_when_first_provider_fails():
    routed = await Router([FakeProvider("a", fail=True), FakeProvider("b", reply="ok")]).translate(REQUEST)
    assert routed.provider == "b"


async def test_falls_back_on_timeout():
    router = Router([FakeProvider("slow", delay=1.0), FakeProvider("fast", reply="rápido")], timeout_s=0.05)
    routed = await router.translate(REQUEST)
    assert routed.provider == "fast"


async def test_stalled_session_is_retried_instead_of_failing():
    class StallsOnce(FakeProvider):
        async def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                await asyncio.sleep(10)  # sesión colgada: nunca responde
            for word in self.reply.split(" "):
                yield word + " "

    provider = StallsOnce(reply="hola de nuevo")
    routed = await Router([provider], timeout_s=2.0, first_token_s=0.05).translate(REQUEST)
    assert routed.text == "hola de nuevo"
    assert len(provider.requests) == 2


async def test_slow_but_streaming_answer_is_not_cut_at_the_first_token_limit():
    class Slow(FakeProvider):
        async def stream(self, request):
            yield "hola "
            await asyncio.sleep(0.2)  # ya empezó a responder: tiene el tiempo completo
            yield "amigo"

    routed = await Router([Slow()], timeout_s=2.0, first_token_s=0.1).translate(REQUEST)
    assert routed.text == "hola amigo"


async def test_raises_when_all_fail():
    with pytest.raises(AllProvidersFailed):
        await Router([FakeProvider("a", fail=True)]).translate(REQUEST)
