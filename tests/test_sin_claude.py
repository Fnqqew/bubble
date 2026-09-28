"""Sin Claude: Bubble Pro traduce con el agente de Deepgram (con un Deepgram de mentira: nunca gasta crédito), se
detecta quién no tiene Claude y la ventana «Para traducir, Bubble necesita Claude» guía los dos caminos."""

import asyncio
import json
import queue
import subprocess
import time
import tkinter as tk

import pytest

from bubble import install, system
from bubble.cloud import agent
from bubble.cloud.errors import CloudError, NoCredit
from bubble.translate.base import TranslationRequest
from bubble.translate.router import SENT


# ---------------------------------------------------------------- un agente de Deepgram de mentira
class FakeAgent:
    """Como el de Deepgram: Settings → SettingsApplied; cada pedido → la respuesta, la voz (bytes) y AgentAudioDone.
    Si le mandan un pedido mientras «habla», corta (como el de verdad)."""

    def __init__(self, replies=None, error=None, drop_after=None):
        self.replies = replies or (lambda content: "<t1>hola</t1>")
        self.error = error  # un Error en vez de responder
        self.drop_after = drop_after  # se corta después de tantos pedidos
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.speaking = False
        self.closed = False
        self.asked = 0

    async def send(self, message):
        data = json.loads(message)
        self.sent.append(data)
        kind = data["type"]
        if kind == "Settings":
            await self.inbox.put(json.dumps({"type": "SettingsApplied"}))
        elif kind == "InjectUserMessage":
            if self.speaking:
                await self.inbox.put(None)  # corta la conexión
                return
            self.asked += 1
            if self.drop_after is not None and self.asked > self.drop_after:
                await self.inbox.put(None)
                return
            if self.error:
                await self.inbox.put(json.dumps({"type": "Error", **self.error}))
                return
            self.speaking = True
            await self.inbox.put(json.dumps({"type": "ConversationText", "role": "user", "content": data["content"]}))
            await self.inbox.put(json.dumps({"type": "ConversationText", "role": "assistant",
                                             "content": self.replies(data["content"])}))
            await self.inbox.put(b"\x00" * 3200)  # la voz del agente (se ignora)
            asyncio.get_running_loop().call_later(0.05, self._done_speaking)

    def _done_speaking(self):
        self.speaking = False
        self.inbox.put_nowait(json.dumps({"type": "AgentAudioDone"}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.inbox.get()
        if message is None or self.closed:
            self.closed = True
            raise StopAsyncIteration
        return message

    async def close(self):
        self.closed = True
        await self.inbox.put(None)


def provider_with(agents, **kwargs):
    made = []

    async def connect(url, **options):
        assert url == agent.URL and options["additional_headers"]["Authorization"] == "Token clave"
        fake = agents.pop(0) if agents else FakeAgent()
        made.append(fake)
        return fake

    counted = []
    provider = agent.DeepgramAgentProvider("clave", "Sos un traductor.", connect=connect, count=counted.append,
                                           **kwargs)
    return provider, made, counted


async def ask(provider, *texts):
    requests = [TranslationRequest(text, "es", "incoming") for text in texts]
    pieces = [item async for item in provider.stream_batch(requests)]
    assert pieces[0] == (SENT, "")
    return pieces[1:]


async def test_translates_through_the_agent_with_the_same_format():
    provider, made, _ = provider_with([FakeAgent(lambda content: "<t1>gracias</t1><t2>posta</t2>")])
    assert await ask(provider, "vlw", "fr") == [(0, "gracias"), (1, "posta")]
    settings = made[0].sent[0]
    assert settings["agent"]["think"]["provider"] == {"type": "anthropic", "model": "claude-haiku-4-5"}
    assert settings["agent"]["think"]["prompt"] == "Sos un traductor."
    assert made[0].sent[1]["type"] == "InjectUserMessage" and "vlw" in made[0].sent[1]["content"]
    await provider.close()


async def test_waits_for_the_agent_to_finish_speaking_before_the_next_message():
    provider, made, _ = provider_with([FakeAgent()])
    await ask(provider, "uno")
    await ask(provider, "dos")  # si se lo mandara mientras «habla», el agente cortaría
    assert len(made) == 1 and made[0].asked == 2
    await provider.close()


async def test_opens_only_when_needed_and_closes_when_the_chat_is_quiet():
    provider, made, counted = provider_with([FakeAgent()], idle_close_s=0.2)
    await provider.start()
    await provider.warm_up()
    assert made == []  # abrir la conexión se cobra: recién con la primera traducción
    await ask(provider, "uno")
    await asyncio.sleep(0.4)
    assert made[0].closed and provider._conn is None
    assert len(counted) == 1 and 0 < counted[0] < 2  # se cobra el rato que estuvo abierta


async def test_if_the_connection_drops_it_reconnects_and_asks_again():
    provider, made, _ = provider_with([FakeAgent(drop_after=1), FakeAgent(lambda content: "<t1>de nuevo</t1>")])
    await ask(provider, "uno")
    assert await ask(provider, "dos") == [(0, "de nuevo")]
    assert len(made) == 2
    await provider.close()


async def test_no_credit_warns_once_and_fails():
    fatal = []
    provider, _, _ = provider_with([FakeAgent(error={"code": "INSUFFICIENT_CREDITS", "description": "no balance"})],
                                   on_fatal=fatal.append)
    with pytest.raises(NoCredit):
        await ask(provider, "uno")
    assert len(fatal) == 1 and isinstance(fatal[0], NoCredit)


def test_agent_errors_are_explained():
    assert isinstance(agent.agent_error({"description": "Insufficient credits"}), NoCredit)
    assert type(agent.agent_error({"code": "CLIENT_MESSAGE_TIMEOUT"})) is CloudError


async def test_starts_a_fresh_conversation_every_so_often(monkeypatch):
    monkeypatch.setattr(agent, "MAX_TURNS", 2)
    provider, made, _ = provider_with([FakeAgent(), FakeAgent()])
    for text in ("uno", "dos", "tres"):
        await ask(provider, text)
    assert len(made) == 2  # (el agente arrastra toda la conversación: después de tanto, una nueva)
    await provider.close()


def test_without_claude_the_translator_uses_the_agent():
    from bubble.config import Config
    from bubble.translate import build_translator

    translator = build_translator(Config(), cloud_key="clave")
    lanes = [*translator.router.providers, *translator.voice_router.providers]
    assert [type(p).__name__ for p in lanes] == ["DeepgramAgentProvider"] * 2  # chat y voz, cada uno por su lado
    assert translator.hedge is False  # la voz nunca abre dos conexiones para lo mismo
    assert build_translator(Config()).router.providers[0].name == "claude"


# ---------------------------------------------------------------- quién no tiene Claude
@pytest.mark.parametrize("claude, problem", [
    (system.Claude(), "sin_claude"),
    (system.Claude(installed=True, logged_in=False), "sin_sesion"),
    (system.Claude(installed=True, logged_in=True, plan="free", auth="claude.ai"), "gratis"),
    (system.Claude(installed=True, logged_in=True, auth="console"), "por_uso"),
    (system.Claude(installed=True, logged_in=True, plan="max", auth="claude.ai"), ""),
    (system.Claude(installed=True), ""),  # (un Claude Code viejo que no dice nada: no se asume nada)
])
def test_knows_what_is_missing_to_translate_with_claude(claude, problem):
    assert system.claude_problem(claude) == problem


def test_without_claude_it_recommends_bubble_pro_with_the_free_credits():
    info = system.System(build=26200, threads=8, ram_gb=16, microphones=["m"], ocr_languages=["en"], roblox="x",
                         claude=system.Claude(installed=True, logged_in=True, plan="free", auth="claude.ai"))
    advice = [item for item in system.recommend(info).advice if item.action == "claude"]
    assert len(advice) == 1 and "Bubble Pro" in advice[0].text and "200 US$" in advice[0].text


def test_reads_how_claude_code_is_logged_in(monkeypatch):
    import bubble.claude_cli

    monkeypatch.setattr(bubble.claude_cli, "find_claude_cli", lambda path=None: "claude.exe")
    monkeypatch.setattr(bubble.claude_cli, "cli_version", lambda path: (2, 1, 0))
    answer = json.dumps({"loggedIn": True, "authMethod": "console", "subscriptionType": None})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, answer, ""))
    status = system.claude_status()
    assert status.logged_in and status.auth == "console" and status.plan == ""
    assert system.claude_problem(status) == "por_uso"


def test_claude_is_no_longer_required_to_install_and_login_goes_to_the_browser(monkeypatch):
    steps = {step.key: step for step in install.steps()}
    assert not steps["claude"].required and not steps["sesion"].required  # sin Claude, Pro traduce
    import bubble.claude_cli

    opened = []
    monkeypatch.setattr(bubble.claude_cli, "find_claude_cli", lambda path=None: "claude.exe")
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: opened.append(command))
    install._login_claude(lambda *a: None)
    assert opened == [["claude.exe", "auth", "login", "--claudeai"]]


# ---------------------------------------------------------------- la ventana
class FakeApp:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.calls = []

    def use_cloud_translation(self):
        self.calls.append("pro")

    def claude_connected(self):
        self.calls.append("claude")

    def open_no_claude(self, reason=""):
        pass


@pytest.fixture
def window(monkeypatch):
    from bubble.ui import widgets
    from bubble.ui.no_claude_window import NoClaudeWindow

    monkeypatch.setattr(widgets, "present", lambda window, root: None)  # escondida: nunca te saca el foco
    root = tk.Tk()
    root.withdraw()
    app = FakeApp(root)
    try:
        yield lambda reason="gratis": (NoClaudeWindow(app, reason), app)
    finally:
        root.destroy()


def pump(app, dialog, until, seconds=4.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        while not app.events.empty():
            _kind, action = app.events.get_nowait()
            action()
        dialog.window.update()
        time.sleep(0.02)


def test_warns_about_the_cost_before_activating_pro(window):
    from bubble import pro

    dialog, _app = window()
    texts = []

    def collect(widget):
        for child in widget.winfo_children():
            if child.winfo_class() == "TLabel":
                texts.append(str(child.cget("text")))
            collect(child)

    collect(dialog.window)
    assert pro.NO_CLAUDE_WARNING in texts and any("200 US$" in text for text in texts)


def test_pasting_a_good_key_activates_pro(window, monkeypatch):
    import bubble.cloud.deepgram
    import bubble.cloud.keys

    saved = []
    monkeypatch.setattr(bubble.cloud.deepgram, "check_key", lambda key: (True, "La clave anda."))
    monkeypatch.setattr(bubble.cloud.keys, "save_key", saved.append)
    dialog, app = window()
    dialog.key_var.set("  mi-clave  ")
    dialog.activate.invoke()
    pump(app, dialog, lambda: app.calls)
    assert saved == ["mi-clave"] and app.calls == ["pro"]


def test_a_bad_key_says_why(window, monkeypatch):
    import bubble.cloud.deepgram

    monkeypatch.setattr(bubble.cloud.deepgram, "check_key", lambda key: (False, "Deepgram no acepta la clave."))
    dialog, app = window()
    dialog.key_var.set("mala")
    dialog.activate.invoke()
    pump(app, dialog, lambda: "no acepta" in str(dialog.key_state.cget("text")))
    assert "no acepta" in str(dialog.key_state.cget("text")) and not app.calls
    assert "disabled" not in dialog.activate.state()


def test_logging_in_to_claude_then_checking_reconnects(window, monkeypatch):
    logins = []
    monkeypatch.setattr(install, "_login_claude", lambda progress: logins.append(1))
    monkeypatch.setattr(system, "claude_status", lambda: system.Claude(installed=True, logged_in=True, plan="pro",
                                                                       auth="claude.ai"))
    dialog, app = window("sin_sesion")
    dialog.login.invoke()
    assert logins and str(dialog.login.cget("text")) == "Listo, revisar"
    dialog.login.invoke()
    pump(app, dialog, lambda: app.calls)
    assert app.calls == ["claude"]


def test_without_claude_code_it_offers_to_install_it(window, monkeypatch):
    installs = []
    monkeypatch.setattr(install, "_install_claude", lambda progress: installs.append(1))
    dialog, _app = window("sin_claude")
    assert str(dialog.login.cget("text")) == "Instalar Claude Code"
    dialog.login.invoke()
    assert installs and str(dialog.login.cget("text")) == "Iniciar sesión en Claude"
