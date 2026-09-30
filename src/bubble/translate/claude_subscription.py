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
from .router import SENT

log = logging.getLogger(__name__)
hidden_processes.install()  # evita ventanas de consola de claude.exe
KEEP_WARM_AFTER_S = 240  # el caché del prompt expira a los 5 minutos
SHRINK_AFTER_S = 300  # las sesiones sobrantes se cierran tras este tiempo sin pedidos superpuestos


class ProviderError(RuntimeError):
    pass


def _session_dir() -> Path:
    # Carpeta vacía propia: Claude Code no carga CLAUDE.md ni archivos de ningún proyecto.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "claude-session"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Session:
    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.client: ClaudeSDKClient | None = None
        self.turns = 0
        self.refreshing = False  # ya se está abriendo su reemplazo
        self.retired = False  # ya existe su reemplazo: se cierra al quedar libre

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
    # Los totales de cada ResultMessage son acumulados de la sesión: se guarda el último para sumar solo las
    # diferencias.
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
    reports_sent = True  # avisa (SENT) al enviar el pedido: la espera de turno no cuenta como sesión bloqueada

    def __init__(self, config: ClaudeConfig, system_prompt: str = SYSTEM_PROMPT, model: str = "",
                 min_sessions: int = 1, thinking: bool = True, name: str = "claude") -> None:
        """`model`: modelo distinto del configurado (la voz usa uno más rápido). `thinking=False`: responde sin
        razonamiento previo, innecesario para traducir y reduce la latencia de la primera palabra.
        """
        self.config = config
        self.name = name
        self.model = model or config.model
        self.min_sessions = max(1, min_sessions)
        self._options = ClaudeAgentOptions(
            cli_path=find_claude_cli(config.cli_path),
            model=self.model,
            effort=config.effort,
            thinking=None if thinking else {"type": "disabled"},
            system_prompt=system_prompt,
            tools=[],
            setting_sources=[],
            strict_mcp_config=True,
            skills=[],
            max_turns=1,
            include_partial_messages=True,
            # El texto proviene de otros jugadores: nunca se expanden @rutas ni /comandos.
            verbatim_prompts=True,
            cwd=_session_dir(),
            env={
                "CLAUDE_AGENT_SDK_CLIENT_APP": f"bubble/{__version__}",
                # Sin tráfico no esencial: evita una llamada adicional a Haiku por mensaje, sin beneficio.
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            stderr=lambda line: log.debug("claude: %s", line),
        )
        self.usage = UsageStats()
        self._idle: asyncio.Queue[_Session] = asyncio.Queue()
        self._all: set[_Session] = set()
        self._retired: set[_Session] = set()
        self._replacing: set[asyncio.Task] = set()
        # Mantiene el prompt cacheado activo mientras esta función indique que hay una partida en curso.
        self.keep_warm_when: Callable[[], bool] | None = None
        self._last_used = time.monotonic()
        self._last_crowded = 0.0  # momento en que llegó un pedido con todas las sesiones ocupadas
        self._opening = 0
        self._keepalive: asyncio.Task | None = None

    async def start(self) -> None:
        """Arranca con una sesión: cada una es un claude.exe de 150 a 300 MB y, como los mensajes simultáneos se
        envían en un mismo pedido, normalmente alcanza. Si llegan pedidos con todas ocupadas se abren más (hasta
        `pool_size`), y las sobrantes se cierran solas tras un período de poca actividad.
        """
        error: BaseException | None = None
        for _attempt in range(2):
            session = _Session(self._options)
            try:
                await session.open()
            except Exception as exc:  # noqa: BLE001 - se reintenta una vez
                error = exc
                await self._close_quietly(session)
                continue
            self._all.add(session)
            self._idle.put_nowait(session)
            break
        else:
            raise ProviderError(f"No se pudo iniciar Claude: {error}") from error
        for _extra in range(self.min_sessions - 1):
            self._opening += 1
            self._spawn(self._open_extra())
        self._keepalive = asyncio.get_running_loop().create_task(self._keep_warm())

    async def warm_up(self) -> None:
        """Pedido mínimo que deja el prompt en el caché de este modelo, para que el primer pedido real no espere."""
        try:
            async for _ in self.stream(TranslationRequest("ok", "en", "incoming")):
                pass
        except Exception:  # noqa: BLE001 - es solo una optimización
            log.debug("No se pudo precalentar %s", self.name, exc_info=True)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._replacing.add(task)
        task.add_done_callback(self._replacing.discard)

    def _grow(self) -> None:
        """Todas ocupadas: se abre otra en segundo plano. El pedido toma la primera que se libere (o la nueva)."""
        self._last_crowded = time.monotonic()
        if len(self._all) + self._opening >= max(1, self.config.pool_size):
            return
        self._opening += 1
        self._spawn(self._open_extra())

    async def _open_extra(self) -> None:
        session = _Session(self._options)
        try:
            async with asyncio.timeout(30):
                await session.open()
        except Exception:  # noqa: BLE001 - se sigue con las que hay
            log.warning("No se pudo abrir otra sesión de Claude", exc_info=True)
            await self._close_quietly(session)
            return
        finally:
            self._opening -= 1
        self._all.add(session)
        self._idle.put_nowait(session)

    async def _shrink(self) -> None:
        """Tras un período sin pedidos superpuestos, queda una sola sesión abierta para reducir memoria."""
        if len(self._all) <= self.min_sessions or time.monotonic() - self._last_crowded < SHRINK_AFTER_S:
            return
        while len(self._all) > self.min_sessions and not self._idle.empty():
            session = self._idle.get_nowait()
            self._all.discard(session)
            await self._close_quietly(session)

    async def _keep_warm(self) -> None:
        """El caché del prompt expira a los 5 minutos: si el chat permanece en silencio durante la partida, la
        siguiente traducción tardaría más. Un pedido mínimo cada ~4 minutos de silencio lo mantiene activo.
        """
        while True:
            await asyncio.sleep(30)
            await self._shrink()
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
        await asyncio.gather(*(s.close() for s in self._all | self._retired), return_exceptions=True)
        self._all.clear()
        self._retired.clear()

    async def stream(self, request: TranslationRequest) -> AsyncIterator[str]:
        async for index, text in self.stream_batch([request]):
            if index != SENT:
                yield text

    async def stream_batch(self, requests: list[TranslationRequest]) -> AsyncIterator[tuple[int, str]]:
        """Traduce varios mensajes en un solo pedido y devuelve (índice, fragmento) a medida que llegan."""
        self._last_used = time.monotonic()
        if self._idle.empty():
            self._grow()
        session = await self._take()
        healthy = False
        try:
            if session.client is None:
                async with asyncio.timeout(30):
                    await session.open()
            await session.client.query(build_user_prompt(requests))
            yield SENT, ""
            streamed = False
            fallback_text: list[str] = []
            output = OutputFilter(len(requests))  # solo el contenido de <tN>...</tN>
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
            if not healthy:
                self._spawn(self._replace(session))  # cortada a mitad de respuesta: se reemplaza
            elif session.retired:
                self._retire(session)
            else:
                self._idle.put_nowait(session)
                if session.turns >= self.config.session_max_turns and not session.refreshing:
                    # Historial largo: el reemplazo se abre MIENTRAS esta sesión sigue atendiendo. Con una sola sesión,
                    # cerrarla primero obligaba al pedido siguiente a esperar el arranque de otro claude.exe.
                    session.refreshing = True
                    self._spawn(self._refresh(session))

    async def _take(self) -> _Session:
        while True:
            session = await self._idle.get()
            if not session.retired:
                return session
            self._retire(session)

    def _retire(self, session: _Session) -> None:
        self._retired.discard(session)
        self._spawn(self._close_quietly(session))

    async def _refresh(self, old: _Session) -> None:
        new = _Session(self._options)
        try:
            async with asyncio.timeout(30):
                await new.open()
        except Exception:  # noqa: BLE001 - se sigue con la vieja; se intenta de nuevo más adelante
            log.warning("No se pudo abrir la sesión de reemplazo", exc_info=True)
            await self._close_quietly(new)
            old.refreshing = False
            return
        self._all.add(new)
        self._idle.put_nowait(new)
        self._all.discard(old)
        self._retired.add(old)
        old.retired = True  # si está libre, se cierra cuando se la retire de la cola

    async def _replace(self, old: _Session) -> None:
        # La sesión vieja puede estar bloqueada (por eso se reemplaza) y su cierre también: se cierra aparte, con límite
        # de tiempo, y la nueva se abre sin esperarla. De otro modo el pool se vaciaba progresivamente y todo fallaba.
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
