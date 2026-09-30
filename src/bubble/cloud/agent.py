"""Traducción sin Claude mediante el agente de voz de Deepgram («Voice Agent»), que incluye un modelo de lenguaje
(Claude Haiku 4.5) y se paga con los mismos créditos de Bubble Pro. Está pensado para quien todavía no tiene Claude.

Se usa solo con texto: se envía lo que hay que traducir (InjectUserMessage), con las mismas instrucciones que usa
Bubble con Claude, y el agente responde la traducción en el mismo formato, que se lee con el mismo filtro.

Deepgram cobra por minuto de conexión abierta, no por mensaje (0,075 US$ el minuto). Por eso la conexión se abre
recién cuando hay algo para traducir y se cierra sola a los 20 s sin mensajes. Así, una partida tranquila cuesta
centavos por hora en lugar de 4,50 US$.

El agente también «dice» cada respuesta en voz (no se puede desactivar). Esa voz se ignora, pero hay que esperar a
que termine (AgentAudioDone) antes de enviar el siguiente pedido; de lo contrario, el agente cierra la conexión. Con
una cuenta real se midió ~1 s por traducción y ~1,3 s para abrir la conexión.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Callable

from ..translate.base import TranslationRequest
from ..translate.prompt import OutputFilter, build_user_prompt
from ..translate.router import SENT
from .errors import BadKey, CloudError, NoCredit, error_for

log = logging.getLogger(__name__)
URL = "wss://agent.deepgram.com/v1/agent/converse"
MODEL = ("anthropic", "claude-haiku-4-5")  # el más rápido de los probados que entiende bien la jerga
PRICE_PER_MIN = 0.075  # nivel Standard, deepgram.com/pricing
IDLE_CLOSE_S = 20.0  # sin mensajes durante este tiempo, se cierra (se paga por minuto abierto)
MAX_TURNS = 16  # el agente acumula la conversación: tras estos pedidos se inicia una nueva
KEEPALIVE_S = 2.0  # sin «audio» unos segundos, el agente cierra (CLIENT_MESSAGE_TIMEOUT)
READY_S = 12.0  # tope para abrir la conexión o esperar que el agente termine de «hablar»


def settings(prompt: str) -> dict:
    return {
        "type": "Settings",
        "audio": {"input": {"encoding": "linear16", "sample_rate": 16000},
                  "output": {"encoding": "linear16", "sample_rate": 16000, "container": "none"}},
        "agent": {"listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
                  "think": {"provider": {"type": MODEL[0], "model": MODEL[1]}, "prompt": prompt},
                  "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}}},
    }


def agent_error(data: dict) -> CloudError:
    text = f"{data.get('code', '')} {data.get('description', '')}".lower()
    if any(word in text for word in ("credit", "balance", "insufficient", "payment")):
        return NoCredit("Tu cuenta de Deepgram no tiene saldo")
    if any(word in text for word in ("auth", "unauthorized", "forbidden", "api key", "invalid key")):
        return BadKey("Deepgram no acepta tu clave")
    return CloudError(f"el agente de Deepgram: {data.get('description') or data.get('code') or 'error'}")


class _Connection:
    """Conexión con el agente: atiende un pedido por vez."""

    def __init__(self, key: str, prompt: str, connect=None) -> None:
        self.key, self.prompt = key, prompt
        self._connect = connect
        self.ws = None
        self.ready = asyncio.Event()  # indica que se puede enviar otro pedido
        self.reply: asyncio.Future | None = None
        self.turns = 0
        self.opened_at = 0.0
        self.closed = False
        self.error: CloudError | None = None
        self._reader: asyncio.Task | None = None
        self._alive: asyncio.Task | None = None

    async def open(self) -> None:
        if self._connect is None:
            from websockets.asyncio.client import connect

            self._connect = connect
        from websockets.exceptions import InvalidStatus

        try:
            self.ws = await self._connect(URL, additional_headers={"Authorization": f"Token {self.key}"},
                                          max_size=None, close_timeout=0.5, open_timeout=10)
        except InvalidStatus as exc:
            raise error_for(exc.response.status_code) from exc
        except OSError as exc:
            raise CloudError(f"sin conexión con Deepgram ({exc})") from exc
        self.opened_at = time.monotonic()
        self._reader = asyncio.get_running_loop().create_task(self._read())
        await self.ws.send(json.dumps(settings(self.prompt)))
        self._alive = asyncio.get_running_loop().create_task(self._keep_alive())
        try:
            async with asyncio.timeout(READY_S):
                await self.ready.wait()
        except TimeoutError:
            await self.close()
            raise self.error or CloudError("el agente de Deepgram no respondió")
        if self.error:
            raise self.error

    async def _read(self) -> None:
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    continue  # voz de la respuesta del agente: no se usa
                data = json.loads(message)
                kind = data.get("type")
                if kind in ("SettingsApplied", "AgentAudioDone"):
                    self.ready.set()
                elif kind == "ConversationText" and data.get("role") == "assistant":
                    if self.reply is not None and not self.reply.done():
                        self.reply.set_result(str(data.get("content") or ""))
                elif kind == "InjectionRefused":
                    self._fail(CloudError("el agente estaba ocupado"))
                elif kind == "Error":
                    self.error = agent_error(data)
                    log.warning("Agente de Deepgram: %s", data)
                    self._fail(self.error)
                    break
                elif kind == "Warning":
                    log.info("Agente de Deepgram (aviso): %s", data)
        except Exception as exc:  # noqa: BLE001 - la conexión se cortó
            log.debug("Se cortó la conexión con el agente", exc_info=True)
            self._fail(CloudError(f"se cortó la conexión con Deepgram ({exc})"))
        finally:
            self.closed = True
            self.ready.set()  # evita dejar a nadie esperando: el pedido detecta que se cerró
            self._fail(self.error or CloudError("se cortó la conexión con Deepgram"))

    def _fail(self, error: CloudError) -> None:
        if self.reply is not None and not self.reply.done():
            self.reply.set_exception(error)

    async def send(self, text: str) -> asyncio.Future:
        """Envía un pedido cuando el agente terminó con el anterior. Devuelve el futuro con la respuesta."""
        async with asyncio.timeout(READY_S):
            await self.ready.wait()
        if self.closed:
            raise self.error or CloudError("se cortó la conexión con Deepgram")
        self.ready.clear()
        self.reply = asyncio.get_running_loop().create_future()
        await self.ws.send(json.dumps({"type": "InjectUserMessage", "content": text}))
        self.turns += 1
        return self.reply

    async def _keep_alive(self) -> None:
        """Mantiene la conexión abierta; sin voz entrante, el agente la cierra a los pocos segundos."""
        try:
            while not self.closed:
                await asyncio.sleep(KEEPALIVE_S)
                await self.ws.send(json.dumps({"type": "KeepAlive"}))
        except Exception:  # noqa: BLE001 - se cerró
            pass

    async def close(self) -> float:
        """Cierra la conexión y devuelve los segundos que estuvo abierta (lo que se cobra)."""
        if self.ws is not None and not self.closed:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        for task in (self._reader, self._alive):
            if task is not None:
                task.cancel()
        self.closed = True
        return time.monotonic() - self.opened_at if self.opened_at else 0.0


class DeepgramAgentProvider:
    """Traduce con el agente de Deepgram (ver arriba). Mismo formato que ClaudeSubscriptionProvider."""

    reports_sent = True  # avisa (SENT) al enviar el pedido: abrir la conexión no cuenta como demora

    def __init__(self, key: str, prompt: str, name: str = "deepgram", idle_close_s: float = IDLE_CLOSE_S,
                 on_fatal: Callable[[CloudError], None] | None = None, connect=None,
                 count: Callable[[float], None] | None = None) -> None:
        self.key, self.prompt, self.name = key, prompt, name
        self.idle_close_s = idle_close_s
        self.on_fatal = on_fatal  # se invoca sin saldo o con clave inválida (se avisa en la ventana)
        self._connect = connect
        self._count = count if count is not None else _count_seconds
        self._conn: _Connection | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.Task | None = None

    async def start(self) -> None:
        """No abre nada: la conexión se cobra por minuto, por lo que se abre con la primera traducción."""

    async def warm_up(self) -> None:
        """No hace nada a propósito: precalentar implicaría pagar minutos sin traducir."""

    async def close(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        await self._drop()

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            seconds = await conn.close()
            if seconds:
                self._count(seconds)

    async def _connection(self) -> _Connection:
        if self._conn is not None and (self._conn.closed or self._conn.turns >= MAX_TURNS):
            await self._drop()
        if self._conn is None:
            conn = _Connection(self.key, self.prompt, self._connect)
            await conn.open()
            self._conn = conn
        return self._conn

    async def stream_batch(self, requests: list[TranslationRequest]) -> AsyncIterator[tuple[int, str]]:
        if self._idle is not None:
            self._idle.cancel()
        try:
            async with self._lock:  # el agente responde un pedido por vez
                sent = False
                for attempt in (1, 2):
                    try:
                        conn = await self._connection()
                        reply = await conn.send(build_user_prompt(requests))
                        if not sent:
                            sent = True
                            yield SENT, ""
                        text = await reply
                        break
                    except (BadKey, NoCredit) as exc:
                        await self._drop()
                        if self.on_fatal is not None:
                            self.on_fatal(exc)
                        raise
                    except CloudError:
                        await self._drop()  # conexión cortada: se abre una nueva y se reenvía (una sola vez)
                        if attempt == 2:
                            raise
                output = OutputFilter(len(requests))
                for piece in output.feed(text):
                    yield piece
                for piece in output.finish():
                    yield piece
        finally:
            self._idle = asyncio.get_running_loop().create_task(self._close_when_idle())

    async def stream(self, request: TranslationRequest) -> AsyncIterator[str]:
        async for index, chunk in self.stream_batch([request]):
            if index != SENT:
                yield chunk

    async def _close_when_idle(self) -> None:
        """Mantiene la conexión abierta un tiempo por si llega otro mensaje y la cierra si no llega ninguno."""
        try:
            await asyncio.sleep(self.idle_close_s)
            if not self._lock.locked():
                await self._drop()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - se cerró sola
            log.debug("No se pudo mantener la conexión con el agente", exc_info=True)
            await self._drop()


def _count_seconds(seconds: float) -> None:
    from .. import pro

    pro.count_translation(seconds)
