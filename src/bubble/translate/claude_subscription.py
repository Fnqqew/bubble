"""Proveedor de traducción que usa la suscripción de Claude del jugador.

Mantiene un pool de sesiones de Claude Code abiertas (vía Claude Agent SDK) para
evitar el costo de arrancar un proceso por mensaje. Cada sesión corre sin
herramientas, sin MCP ni configuración del usuario: solo traduce.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from .. import __version__, hidden_processes
from ..claude_cli import find_claude_cli
from ..config import ClaudeConfig
from .base import TranslationRequest
from .prompt import SYSTEM_PROMPT, OutputFilter, build_user_prompt

log = logging.getLogger(__name__)
hidden_processes.install()  # sin ventanas negras de claude.exe
KEEP_WARM_AFTER_S = 240  # el caché del prompt dura 5 minutos


class ProviderError(RuntimeError):
    pass


def _session_dir() -> Path:
    # Carpeta vacía propia: Claude Code no levanta CLAUDE.md ni archivos de ningún proyecto.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "claude-session"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Session:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.client: ClaudeSDKClient | None = None
        self.turns = 0

    async def open(self) -> None:
        client = ClaudeSDKClient(options=self.options)
        await client.connect()
        self.client = client

    async def close(self) -> None:
        if self.client is not None:
            with suppress(Exception):
                await self.client.disconnect()
            self.client = None


@dataclass
class UsageStats:
    """Consumo acumulado: tokens, costo equivalente en API y estado de los límites de la suscripción."""

    requests: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_by_model: dict[str, float] = field(default_factory=dict)
    # rate_limit_type ("five_hour", "seven_day", ...) -> utilization (0.0 a 1.0)
    utilization: dict[str, float] = field(default_factory=dict)
    # Los totales de cada ResultMessage son acumulados de la sesión: se guarda el último para sumar diferencias.
    _last_by_session: dict[str, dict[str, float]] = field(default_factory=dict)

    def add_result(self, message: ResultMessage) -> None:
        self.requests += 1
        model_usage = message.model_usage or {}
        current = {"__cost__": message.total_cost_usd or 0.0}
        for model, info in model_usage.items():
            current[f"cost:{model}"] = info.get("costUSD", 0.0) or 0.0
            for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"):
                current[f"{key}:{model}"] = info.get(key, 0) or 0
        previous = self._last_by_session.get(message.session_id, {})
        self._last_by_session[message.session_id] = current
        delta = {key: value - previous.get(key, 0) for key, value in current.items()}
        self.cost_usd += delta["__cost__"]
        for key, value in delta.items():
            kind, _, model = key.partition(":")
            if kind == "cost":
                self.cost_by_model[model] = self.cost_by_model.get(model, 0.0) + value
            elif kind == "inputTokens":
                self.input_tokens += int(value)
            elif kind == "outputTokens":
                self.output_tokens += int(value)
            elif kind == "cacheReadInputTokens":
                self.cache_read_tokens += int(value)
            elif kind == "cacheCreationInputTokens":
                self.cache_write_tokens += int(value)

    def add_rate_limit(self, event: RateLimitEvent) -> None:
        info = event.rate_limit_info
        if info.rate_limit_type and info.utilization is not None:
            self.utilization[info.rate_limit_type] = info.utilization


class ClaudeSubscriptionProvider:
    name = "claude"

    def __init__(self, config: ClaudeConfig, system_prompt: str = SYSTEM_PROMPT) -> None:
        self.config = config
        self._options = ClaudeAgentOptions(
            cli_path=find_claude_cli(config.cli_path),
            model=config.model,
            effort=config.effort,
            system_prompt=system_prompt,
            tools=[],
            setting_sources=[],
            strict_mcp_config=True,
            skills=[],
            max_turns=1,
            include_partial_messages=True,
            # El texto viene de otros jugadores: nunca expandir @rutas ni /comandos.
            verbatim_prompts=True,
            cwd=_session_dir(),
            env={
                "CLAUDE_AGENT_SDK_CLIENT_APP": f"bubble/{__version__}",
                # Sin tráfico no esencial: evita una llamada extra a Haiku por cada mensaje (gasto sin beneficio).
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            stderr=lambda line: log.debug("claude: %s", line),
        )
        self.usage = UsageStats()
        self._idle: asyncio.Queue[_Session] = asyncio.Queue()
        self._all: set[_Session] = set()
        self._replacing: set[asyncio.Task] = set()
        # Mantener "caliente" el prompt cacheado mientras esta función diga que se está jugando.
        self.keep_warm_when: Callable[[], bool] | None = None
        self._last_used = time.monotonic()
        self._keepalive: asyncio.Task | None = None

    async def start(self) -> None:
        sessions = [_Session(self._options) for _ in range(max(1, self.config.pool_size))]
        results = await asyncio.gather(*(s.open() for s in sessions), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if len(errors) == len(sessions):
            raise ProviderError(f"No se pudo iniciar Claude: {errors[0]}") from errors[0]
        for session in sessions:
            self._all.add(session)
            self._idle.put_nowait(session)
        self._keepalive = asyncio.get_running_loop().create_task(self._keep_warm())

    async def _keep_warm(self) -> None:
        """El caché del prompt vence a los 5 minutos: si el chat está callado mientras jugás, la siguiente
        traducción tardaría más. Un pedido mínimo cada ~4 minutos de silencio lo mantiene vivo."""
        while True:
            await asyncio.sleep(30)
            if self.keep_warm_when is None or time.monotonic() - self._last_used < KEEP_WARM_AFTER_S:
                continue
            try:
                if not self.keep_warm_when():
                    continue
                async for _ in self.stream(TranslationRequest("ok", "en", "incoming")):
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - es solo una optimización
                log.debug("No se pudo mantener caliente el caché", exc_info=True)

    async def close(self) -> None:
        if self._keepalive:
            self._keepalive.cancel()
        for task in list(self._replacing):
            task.cancel()
        await asyncio.gather(*(s.close() for s in self._all), return_exceptions=True)
        self._all.clear()

    async def stream(self, request: TranslationRequest) -> AsyncIterator[str]:
        async for _index, text in self.stream_batch([request]):
            yield text

    async def stream_batch(self, requests: list[TranslationRequest]) -> AsyncIterator[tuple[int, str]]:
        """Traduce varios mensajes en un solo pedido; devuelve (índice, fragmento) a medida que llegan."""
        self._last_used = time.monotonic()
        session = await self._idle.get()
        healthy = False
        try:
            if session.client is None:
                async with asyncio.timeout(30):
                    await session.open()
            await session.client.query(build_user_prompt(requests))
            streamed = False
            fallback_text: list[str] = []
            output = OutputFilter(len(requests))  # solo lo que viene dentro de <tN>...</tN>
            async for message in session.client.receive_response():
                if isinstance(message, StreamEvent):
                    event = message.event
                    delta = event.get("delta") or {}
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        streamed = True
                        for piece in output.feed(delta["text"]):
                            yield piece
                elif isinstance(message, AssistantMessage):
                    fallback_text.extend(b.text for b in message.content if isinstance(b, TextBlock))
                elif isinstance(message, RateLimitEvent):
                    self.usage.add_rate_limit(message)
                elif isinstance(message, ResultMessage):
                    self.usage.add_result(message)
                    if message.is_error:
                        raise ProviderError(message.result or message.subtype or "error de Claude")
            if not streamed and fallback_text:
                for piece in output.feed("".join(fallback_text)):
                    yield piece
            for piece in output.finish():
                yield piece
            session.turns += 1
            healthy = True
        finally:
            if healthy and session.turns < self.config.session_max_turns:
                self._idle.put_nowait(session)
            else:
                # Sesión cortada a mitad de respuesta o con historial largo: se reemplaza en segundo plano.
                task = asyncio.get_running_loop().create_task(self._replace(session))
                self._replacing.add(task)
                task.add_done_callback(self._replacing.discard)

    async def _replace(self, old: _Session) -> None:
        # La sesión vieja puede estar colgada (por eso se reemplaza) y cerrarla también puede colgarse: se cierra
        # aparte, con límite, y la nueva se abre sin esperarla. Antes el pool se iba vaciando y todo fallaba.
        self._all.discard(old)
        closing = asyncio.get_running_loop().create_task(self._close_quietly(old))
        self._replacing.add(closing)
        closing.add_done_callback(self._replacing.discard)
        new = _Session(self._options)
        try:
            async with asyncio.timeout(30):
                await new.open()
        except Exception:
            log.exception("No se pudo reabrir la sesión de Claude; se reintenta en el próximo pedido")
            await self._close_quietly(new)
        self._all.add(new)
        self._idle.put_nowait(new)

    @staticmethod
    async def _close_quietly(session: _Session) -> None:
        try:
            async with asyncio.timeout(10):
                await session.close()
        except (Exception, TimeoutError):  # noqa: BLE001 - se abandona; el proceso muere al cerrar Bubble
            log.warning("No se pudo cerrar una sesión de Claude colgada")
            session.client = None
