"""Bubble Pro: reconocimiento de voz en la nube (Deepgram), sin conexión real."""

import asyncio
import json
import math
import queue
import threading

import numpy as np
import pytest

from bubble import pro
from bubble.cloud import deepgram
from bubble.cloud.deepgram import (BadKey, CloudError, DeepgramListener, NoCredit, WithFallback, _error_for,
                                   language_param, parse_clip)


@pytest.fixture(autouse=True)
def _pro_off(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))  # el uso del mes y la clave se guardan en un state.json de prueba
    yield
    pro.set_active(False)


def test_languages_mixed_in_a_phrase_use_multi():
    assert language_param("es-AR") == "multi"
    assert language_param("en") == "multi"
    assert language_param(None) == "multi"
    assert language_param("zh-CN") == "zh"  # salvo los idiomas que se mezclan, este se envía solo


def test_errors_say_what_happened():
    assert isinstance(_error_for(401), BadKey)
    assert isinstance(_error_for(403), BadKey)
    assert isinstance(_error_for(402), NoCredit)
    error = _error_for(503)
    assert isinstance(error, CloudError) and not isinstance(error, (BadKey, NoCredit))


def test_a_phrase_from_the_cloud_looks_like_whisper():
    data = {"results": {"channels": [{"alternatives": [{
        "transcript": "¿Hacemos pvp?", "confidence": 0.93,
        "words": [{"word": "hacemos", "language": "es"}, {"word": "pvp", "language": "en"},
                  {"word": "ya", "language": "es"}]}]}]}}
    heard = parse_clip(data, "es-AR", seconds=1.4, took=0.35)
    assert heard.text == "¿Hacemos pvp?"
    assert heard.language == "es"  # la mayoría de las palabras (pvp es un préstamo)
    assert heard.no_speech == 0.0 and heard.seconds == 1.4 and heard.took == 0.35
    assert heard.logprob == pytest.approx(math.log(0.93))
    assert parse_clip({"results": {"channels": [{"alternatives": [{"transcript": " "}]}]}}, "es", 1, 0.1) is None


def test_if_the_cloud_fails_your_pc_understands_that_phrase():
    failures = []

    class Cloud:
        def transcribe(self, audio, **kwargs):
            raise CloudError("sin conexión")

    class Local:
        def transcribe(self, audio, **kwargs):
            return f"local {kwargs['language']}"

    asr = WithFallback(Cloud(), Local(), failures.append)
    assert asr.transcribe(np.zeros(10), language="es") == "local es"
    assert [str(error) for error in failures] == ["sin conexión"]


# ---------------------------------------------------------------- en vivo
def _results(text, start, duration, final=False, speech_final=False, words=()):
    return {"type": "Results", "start": start, "duration": duration, "is_final": final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text, "words": list(words)}]}}


def _word(text, speaker, language="en", confidence=0.95):
    return {"word": text, "speaker": speaker, "language": language, "confidence": confidence}


def test_live_phrases_grow_and_end_with_who_said_them():
    captions = []
    listener = DeepgramListener("clave", captions.append, source_factory=lambda: None)
    listener.handle(_results("trade me", 0.0, 0.8, words=[_word("trade", 1), _word("me", 1)]))
    listener.handle(_results("trade me your", 0.0, 1.0, final=True,
                             words=[_word("trade", 1), _word("me", 1), _word("your", 1)]))
    listener.handle(_results("sword", 1.0, 0.5, final=True, speech_final=True, words=[_word("sword", 1, confidence=0.9)]))
    listener.handle(_results("hola", 3.0, 0.4, final=True, words=[_word("hola", 0, "es", 0.6)]))
    listener.handle({"type": "UtteranceEnd"})

    first = [c for c in captions if c.id == captions[0].id]
    assert [c.text for c in first] == ["trade me", "trade me your", "trade me your sword"]
    assert [c.final for c in first] == [False, False, True]
    assert first[-1].speaker == 2 and first[-1].language == "en" and first[-1].sure  # Voz 2 (Deepgram numera desde 0)
    second = captions[-1]
    assert second.id != first[-1].id and second.final and second.text == "hola"
    assert second.language == "es" and second.speaker == 1 and not second.sure


def test_only_voice_is_sent_and_the_phrase_is_closed_after_the_pause():
    class Vad:
        speaking = False

        def feed(self, samples):
            return np.array([0.9 if self.speaking else 0.1])

    vad = Vad()
    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None, vad=vad)
    block = np.full(1600, 0.1, dtype=np.float32)  # 0,1 s
    for _ in range(10):
        listener.feed(block)  # silencio: no se envía nada
    assert listener._outbox.empty()
    vad.speaking = True
    for _ in range(5):
        listener.feed(block)
    vad.speaking = False
    for _ in range(14):
        listener.feed(block)
    sent = []
    while not listener._outbox.empty():
        sent.append(listener._outbox.get())
    audio = [item for item in sent if isinstance(item, bytes)]
    assert sent[-1] is deepgram._FINALIZE  # fin del habla: Deepgram cierra la frase de inmediato
    seconds = sum(len(item) for item in audio) / 2 / 16000
    # un poco de audio previo, la voz y una pausa breve; no se cobran los 2,9 s ni el resto del silencio
    assert 0.4 + 0.5 + 0.4 <= seconds <= 0.4 + 0.5 + 0.6
    listener._flush_usage()
    minutes, cost = pro.month_usage()
    assert minutes == pytest.approx(seconds / 60, abs=0.01) and cost > 0


def test_turning_it_off_and_on_quickly_does_not_mix_the_old_threads():
    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None)
    runs, outboxes = [], []
    listener._capture = lambda run: runs.append(run)
    listener._network = lambda run, outbox: outboxes.append(outbox)
    listener.start()
    listener._threads[-1].join(1)
    old_run, old_outbox = runs[0], outboxes[0]
    listener.stop()
    listener.start()
    for thread in listener._threads:
        thread.join(1)
    assert not old_run.is_set() and runs[1].is_set()
    assert outboxes[1] is not old_outbox and listener.running
    listener.stop()
    only_network = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None)
    only_network._capture = lambda run: runs.append("no")
    only_network._network = lambda run, outbox: None
    only_network.start(capture=False)  # voz del jugador con el botón: el audio lo entrega otro componente
    assert len(only_network._threads) == 1 and "no" not in runs
    only_network.stop()


def test_audio_keepalive_finalize_and_close_are_sent_in_order():
    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, item):
            self.sent.append(item)

    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None)
    run, outbox, socket = threading.Event(), queue.Queue(), Socket()
    run.set()
    outbox.put(b"\x01\x00")
    outbox.put(deepgram._FINALIZE)

    async def main():
        task = asyncio.ensure_future(listener._sender(socket, run, outbox))
        await asyncio.sleep(0.3)
        run.clear()
        outbox.put(None)
        await asyncio.wait_for(task, 3)

    asyncio.run(main())
    assert json.loads(socket.sent[0]) == {"type": "KeepAlive"}  # inmediatamente después de conectarse
    assert socket.sent[1] == b"\x01\x00"
    assert json.loads(socket.sent[2]) == {"type": "Finalize"}
    assert json.loads(socket.sent[-1]) == {"type": "CloseStream"}


def test_direct_voice_can_listen_through_the_cloud():
    from bubble.voice.pipelines import DirectVoice, VoiceOut

    class Voices:
        def voice_for(self, _language):
            return None

    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None, language="es-AR",
                                diarize=False)
    direct = DirectVoice(None, VoiceOut(Voices()), lambda text, _how="": (text, "en"), "es-AR", listener=listener)
    assert direct.listener is listener
    assert listener.on_caption == direct._caption
    assert "language=multi" in listener.url() and "diarize=false" in listener.url()


# ---------------------------------------------------------------- tu clave y el aspecto
def test_the_key_is_saved_encrypted(tmp_path):
    from bubble.cloud.keys import load_key, protect, save_key, unprotect
    from bubble.state import state_path

    assert unprotect(protect("dg-clave-123")) == "dg-clave-123"
    save_key("  dg-clave-123 ")
    assert load_key() == "dg-clave-123"
    assert "dg-clave-123" not in state_path().read_text(encoding="utf-8")  # nunca en texto plano
    save_key("")
    assert load_key() == ""


def test_pro_turns_the_accent_gold():
    from bubble.ui import widgets

    blue = widgets.palette()["accent"]
    pro.set_active(True)
    assert widgets.palette()["accent"] == pro.GOLD["oscuro"] != blue
    pro.set_active(False)
    assert widgets.palette()["accent"] == blue


def test_theme_images_turn_gold_and_come_back():
    import tkinter as tk

    sv_ttk = pytest.importorskip("sv_ttk")
    from bubble.ui import theme

    root = tk.Tk()
    root.withdraw()
    try:
        sv_ttk.set_theme("dark")
        names = [str(n) for n in root.tk.call("image", "names") if not str(n).startswith("::")]
        before = {name: bytes(root.tk.call(name, "data", "-format", "png")) for name in names}
        assert theme.tint_accent(root, True) > 10
        changed = [n for n in names if bytes(root.tk.call(n, "data", "-format", "png")) != before[n]]
        assert changed
        assert theme.tint_accent(root, True) == 0  # ya tenían el color dorado
        theme.tint_accent(root, False)
        assert all(bytes(root.tk.call(n, "data", "-format", "png")) == before[n] for n in names)
    finally:
        theme._pairs.clear()
        theme._checked.clear()
        theme._state.update(root=None, gold=False)
        root.destroy()


# ---------------------------------------------------------------- conexiones que quedan abiertas (keep-alive)
class ImpatientServer:
    """Imita a Deepgram: si a una conexión no le llega un pedido a tiempo, escribe un «408» y la cierra."""

    def __init__(self, patience: float = 0.3):
        import socket
        import threading

        self.patience = patience
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self.connections = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        import threading

        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(client, self.connections), daemon=True).start()

    def _serve(self, client, number):
        import socket

        client.settimeout(self.patience)
        buffer = b""
        while True:
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                page = b"<html><body><h1>408 Request Time-out</h1></body></html>"
                client.sendall(b"HTTP/1.1 408 Request Time-out\r\nContent-Length: %d\r\n\r\n%s" % (len(page), page))
                client.close()
                return
            if not chunk:
                client.close()
                return
            buffer += chunk
            if b"\r\n\r\n" in buffer:
                head, _, body = buffer.partition(b"\r\n\r\n")
                length = int(next((line.split(b":")[1] for line in head.split(b"\r\n")
                                   if line.lower().startswith(b"content-length")), b"0"))
                if len(body) >= length:
                    answer = b"ok %d" % number
                    client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(answer), answer))
                    buffer = b""


def local_pool(server):
    import http.client

    from bubble.cloud.connection import Pool

    return Pool("127.0.0.1", 2.0, factory=lambda host, timeout: http.client.HTTPConnection(host, server.port,
                                                                                            timeout=timeout))


def test_a_connection_the_server_gave_up_on_is_not_reused(monkeypatch):
    import time

    from bubble.cloud import connection

    monkeypatch.setattr(connection, "IDLE_MAX_S", 60.0)  # (se descarta por lo que envió el servidor, no por antigüedad)
    server = ImpatientServer()
    pool = local_pool(server)
    pool.warm()
    time.sleep(0.6)  # el servidor ya escribió su «408» y cerró la conexión
    assert pool.request("POST", "/v1/speak", b"hola", {}) == b"ok 2"  # error anterior: «Deepgram respondió 408 <html>…»


def test_a_stale_408_is_retried_with_a_fresh_connection(monkeypatch):
    import time

    from bubble.cloud import connection

    monkeypatch.setattr(connection, "IDLE_MAX_S", 60.0)
    monkeypatch.setattr(connection, "usable", lambda conn: True)  # aunque no se detecte antes de enviar el pedido
    server = ImpatientServer()
    pool = local_pool(server)
    pool.warm()
    time.sleep(0.6)
    assert pool.request("POST", "/v1/speak", b"hola", {}) == b"ok 2"
    pool.warm()
    time.sleep(0.6)
    assert b"".join(pool.stream("POST", "/v1/speak", b"hola", {})) == b"ok 3"


def test_a_quiet_connection_is_dropped_before_deepgram_closes_it():
    from bubble.cloud import connection

    assert connection.IDLE_MAX_S < 5.0  # Deepgram cierra la conexión a los ~5 s (medido)
    server = ImpatientServer(patience=5.0)
    pool = local_pool(server)
    assert pool.request("GET", "/", b"", {}) == b"ok 1"
    assert pool.request("GET", "/", b"", {}) == b"ok 1"  # inmediatamente: misma conexión (más rápido)


def test_errors_never_show_page_code():
    from bubble.cloud.errors import error_for

    assert str(error_for(408, "<html><body><h1>408 Request Time-out</h1>")) == "Deepgram tardó en responder"
    assert str(error_for(503)) == "Deepgram tuvo un problema (503)"
    assert "<" not in str(error_for(400, "<html><body>Bad <b>request</b></body></html>"))
