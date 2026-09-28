"""Tu equipo: Bubble lo detecta solo y se adapta.

Qué se mira (todo en tu PC; nada se manda a ningún lado, salvo la prueba de internet, que descarga unos MB de prueba):
- Windows (versión), Python, procesador, memoria, placa de video, pantalla (tamaño y escala).
- Micrófonos, parlantes y el micrófono virtual.
- Claude Code: si está instalado, si tenés la sesión iniciada y tu plan (de la sesión solo se leen esos datos, nunca
  las claves).
- Roblox: si está instalado (el de roblox.com o el de la Microsoft Store).
- Los idiomas que el lector del chat de Windows sabe leer.
- Internet: cuánto tarda en responder Claude y la nube, y la velocidad de descarga.

Con eso `recommend` decide lo que conviene (cuántas sesiones de Claude, qué reconocimiento de voz, el tamaño de la
ventana…) y arma los avisos para mostrar ("Tu equipo", en Pruebas y en «Acerca de»).
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import socket
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Internet:
    claude_ms: float | None = None  # conectarse con Claude (api.anthropic.com)
    cloud_ms: float | None = None  # conectarse con la nube de Bubble Pro (api.deepgram.com)
    download_mbps: float | None = None
    error: str = ""


@dataclass
class Claude:
    installed: bool = False
    version: str = ""
    logged_in: bool | None = None  # None: no se sabe (un Claude Code viejo no tiene `claude auth status`)
    plan: str = ""  # "pro", "max"… (lo que dice la sesión de Claude Code)
    api_key: bool = False  # hay ANTHROPIC_API_KEY: Claude Code cobraría por uso en vez de usar tu suscripción


@dataclass
class System:
    windows: str = ""
    build: int = 0
    python: str = ""
    python_64bit: bool = True
    cpu: str = ""
    threads: int = 0
    ram_gb: float = 0.0
    gpus: list[str] = field(default_factory=list)
    screen: tuple[int, int] = (0, 0)
    work_area: tuple[int, int] = (0, 0)
    scale: float = 1.0
    ascii_home: bool = True
    microphones: list[str] = field(default_factory=list)
    default_microphone: str = ""
    speakers: list[str] = field(default_factory=list)
    virtual_cable: bool = False
    ocr_languages: list[str] = field(default_factory=list)
    roblox: str = ""  # "roblox.com", "Microsoft Store", o "" (no se encontró)
    claude: Claude = field(default_factory=Claude)
    internet: Internet | None = None

    @property
    def windows_11(self) -> bool:
        return self.build >= 22000

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- lo que se mide
def _windows() -> tuple[str, int]:
    release, version, _csd, _type = platform.win32_ver()
    try:
        build = int(version.split(".")[2])
    except (IndexError, ValueError):
        build = 0
    name = "Windows 11" if build >= 22000 else f"Windows {release}"
    return f"{name} (compilación {build})" if build else name, build


def _screen() -> tuple[tuple[int, int], tuple[int, int], float]:
    """Tamaño real de la pantalla principal, el espacio libre (sin la barra de tareas) y la escala de Windows."""
    from ctypes import wintypes

    from . import win32

    win32.enable_dpi_awareness()  # (si no, Windows informa tamaños achicados: 1536×864 en vez de 1920×1080 al 125 %)
    user32 = ctypes.windll.user32
    size = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    rect = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    work = (rect.right - rect.left, rect.bottom - rect.top)
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except (AttributeError, OSError):
        dpi = 96
    return size, work, round(dpi / 96, 2)


def _audio() -> tuple[list[str], str, list[str], bool]:
    try:
        from .voice import audio as audio_io
        from .voice.devices import is_virtual

        sc = audio_io._sc()
        mics = [m.name for m in sc.all_microphones() if not is_virtual(m.name)]
        default = sc.default_microphone().name
        speakers = [s.name for s in sc.all_speakers() if not is_virtual(s.name)]
        cable = any(is_virtual(m.name) for m in sc.all_microphones())
        return mics, default, speakers, cable
    except Exception:  # noqa: BLE001 - sin la parte de voz no se puede saber
        return [], "", [], False


def _ocr_languages() -> list[str]:
    try:
        from .capture.ocr import _use_system_cpp_runtime

        _use_system_cpp_runtime()  # antes que winrt (si no, la parte de voz se cae después: ver capture/ocr.py)
        from winrt.windows.media.ocr import OcrEngine

        return [language.language_tag for language in OcrEngine.available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


def roblox_installed() -> str:
    """Dónde está Roblox instalado: "roblox.com", "Microsoft Store" o "" (no se encontró)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\roblox-player\shell\open\command") as key:
            if winreg.QueryValueEx(key, "")[0]:
                return "roblox.com"
    except OSError:
        pass
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if any(local.glob("Roblox/Versions/*/RobloxPlayerBeta.exe")) or (local / "Bloxstrap").exists():
        return "roblox.com"
    packages = local / "Packages"
    if packages.exists() and any(packages.glob("ROBLOXCORPORATION.ROBLOX*")):
        return "Microsoft Store"
    return ""


PAID_PLANS = {"pro", "max", "team", "enterprise"}


def claude_status() -> Claude:
    """Claude Code instalado, la sesión y el plan, según Claude Code (`claude auth status`): no se toca ningún archivo
    con claves. Ojo: Claude Code a veces guarda un plan viejo ("pro" teniendo Max): por eso se muestra "Pro o Max"."""
    import subprocess

    from .claude_cli import ClaudeNotFoundError, cli_version, find_claude_cli

    status = Claude(api_key=bool(os.environ.get("ANTHROPIC_API_KEY")))
    try:
        path = find_claude_cli()
    except ClaudeNotFoundError:
        return status
    status.installed = True
    status.version = ".".join(map(str, cli_version(Path(path))))
    try:
        out = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        data = json.loads(out[out.index("{"):out.rindex("}") + 1])
        status.logged_in = bool(data.get("loggedIn"))
        status.plan = str(data.get("subscriptionType") or "")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # no se pudo saber: queda en None (no se avisa nada que no sea seguro)
    return status


def plan_label(plan: str) -> str:
    plan = plan.lower()
    if plan in ("pro", "max"):
        return "Pro o Max"
    return plan.capitalize() if plan else "—"


def _connect_ms(host: str, timeout: float = 4.0) -> float | None:
    """Cuánto tarda en conectarse (la mejor de tres)."""
    best = None
    for _ in range(3):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, 443), timeout=timeout):
                pass
        except OSError:
            continue
        took = (time.perf_counter() - started) * 1000
        best = took if best is None else min(best, took)
    return None if best is None else round(best)


def measure_internet(download_bytes: int = 3_000_000) -> Internet:
    """Cuánto tardan en responder Claude y la nube, y la velocidad de descarga (baja unos MB de prueba)."""
    result = Internet(claude_ms=_connect_ms("api.anthropic.com"), cloud_ms=_connect_ms("api.deepgram.com"))
    try:
        request = urllib.request.Request(f"https://speed.cloudflare.com/__down?bytes={download_bytes}",
                                         headers={"User-Agent": "Bubble"})
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=15) as response:
            size = len(response.read())
        seconds = time.perf_counter() - started
        result.download_mbps = round(size * 8 / seconds / 1e6, 1) if seconds > 0 else None
    except OSError as exc:
        result.error = str(exc)
    if result.claude_ms is None and result.download_mbps is None:
        result.error = result.error or "Sin conexión a internet"
    return result


def detect(internet: bool = False) -> System:
    """Todo lo de tu equipo (~1 s; con `internet`, unos segundos más)."""
    from .voice.checks import cpu_name, gpu_names, memory_gb

    windows, build = _windows()
    size, work, scale = _screen()
    mics, default_mic, speakers, cable = _audio()
    home = str(Path.home())
    info = System(
        windows=windows, build=build, python=platform.python_version(), python_64bit=sys.maxsize > 2 ** 32,
        cpu=cpu_name(), threads=os.cpu_count() or 1, ram_gb=round(memory_gb(), 1), gpus=gpu_names(),
        screen=size, work_area=work, scale=scale, ascii_home=home.isascii(), microphones=mics,
        default_microphone=default_mic, speakers=speakers, virtual_cable=cable, ocr_languages=_ocr_languages(),
        roblox=roblox_installed(), claude=claude_status(),
    )
    if internet:
        info.internet = measure_internet()
    return info


# ---------------------------------------------------------------- qué conviene y qué avisar
@dataclass
class Advice:
    level: str  # "ok", "aviso", "problema"
    text: str


@dataclass
class Plan:
    claude_sessions: int = 3  # sesiones de Claude abiertas a la vez (cada una ~250 MB de memoria)
    whisper_ram_limit: bool = False  # poca memoria: el reconocimiento de voz de tu PC, el más liviano
    slow_internet: bool = False
    advice: list[Advice] = field(default_factory=list)


def sessions_for(ram_gb: float, wanted: int = 3) -> int:
    """Cuántas sesiones de Claude abrir según la memoria (cada una ocupa ~250 MB)."""
    if ram_gb and ram_gb < 6:
        return 1
    if ram_gb and ram_gb < 10:
        return min(wanted, 2)
    return wanted


def recommend(info: System) -> Plan:
    plan = Plan()
    add = plan.advice.append
    if info.build and info.build < 19041:
        add(Advice("problema", "Tu Windows es anterior a la versión 2004: actualizalo (Windows Update). Bubble necesita "
                               "Windows 10 versión 2004 o más nuevo."))
    elif info.build and info.build < 20348:
        add(Advice("aviso", "En tu versión de Windows, los subtítulos escuchan todo el sonido de la PC (no solo Roblox): "
                            "si ponés música, puede aparecer. Con Windows 11 se escucha solo a Roblox."))
    if not info.python_64bit:
        add(Advice("problema", "Tu Python es de 32 bits: la parte de voz no anda. Instalá Python de 64 bits."))
    plan.claude_sessions = sessions_for(info.ram_gb)
    if info.ram_gb and info.ram_gb < 6:
        plan.whisper_ram_limit = True
        add(Advice("aviso", f"Tu PC tiene {info.ram_gb:.0f} GB de memoria: Bubble usa una sola sesión de Claude y el "
                            "reconocimiento de voz más liviano, para no quitarle memoria a Roblox."))
    if info.threads and info.threads < 4:
        add(Advice("aviso", "Tu procesador tiene pocos núcleos: para la voz conviene Bubble Pro (se entiende en la nube)."))
    if not info.claude.installed:
        add(Advice("problema", "Falta Claude Code: es lo que traduce con tu suscripción. Instalalo desde «Revisar "
                               "instalación» (Ajustes)."))
    elif info.claude.logged_in is False:
        add(Advice("problema", "Claude Code no tiene la sesión iniciada: abrí «claude» una vez e iniciá sesión con tu "
                               "cuenta de Claude."))
    elif info.claude.plan and info.claude.plan.lower() not in PAID_PLANS:
        add(Advice("problema", "Tu cuenta de Claude es gratuita: Bubble necesita un plan pago (Pro o Max) para "
                               "traducir con Claude Code."))
    if info.claude.api_key:
        add(Advice("aviso", "Hay una ANTHROPIC_API_KEY en tu PC: Claude Code podría cobrar por uso en vez de usar tu "
                            "suscripción."))
    if not info.roblox:
        add(Advice("aviso", "No encontré Roblox instalado (roblox.com o Microsoft Store)."))
    if not info.ocr_languages:
        add(Advice("problema", "Windows no tiene ningún idioma para leer texto: agregá uno en Configuración › Hora e "
                               "idioma › Idioma (con «Reconocimiento óptico de caracteres»)."))
    if not info.microphones:
        add(Advice("aviso", "No encontré ningún micrófono: tu voz traducida no va a funcionar hasta que conectes uno."))
    if info.work_area[1] and info.work_area[1] / max(info.scale, 1.0) < 700:
        add(Advice("aviso", "Tu pantalla es chica: la ventana de Bubble se achica y se desplaza (usá la ruedita)."))
    net = info.internet
    if net is not None:
        if net.error and net.claude_ms is None:
            add(Advice("problema", "No hay conexión con Claude: revisá tu internet."))
        else:
            slow = (net.claude_ms or 0) > 350 or (net.download_mbps is not None and net.download_mbps < 2)
            plan.slow_internet = slow
            if slow:
                add(Advice("aviso", "Tu internet es lento o está lejos de los servidores: las traducciones van a tardar "
                                    "un poco más."))
    if not any(item.level != "ok" for item in plan.advice):
        add(Advice("ok", "Todo listo: tu PC puede usar todo Bubble."))
    return plan


# ---------------------------------------------------------------- al abrir Bubble
INTERNET_EVERY_S = 24 * 3600  # la velocidad de internet se mide una vez por día (baja 3 MB de prueba)
SLOW_START_S = 0.5  # con internet lento, la voz de la nube arranca con más colchón (no se corta)


def check(internet: bool | None = None) -> tuple[System, Plan]:
    """Revisa tu equipo y dice qué conviene. `internet`: medirlo ya (None: si pasó un día desde la última vez; si no,
    se usa la medición guardada)."""
    from .state import load_state, update_state

    saved = load_state().get("internet") or {}
    if internet is None:
        internet = time.time() - float(saved.get("at", 0)) > INTERNET_EVERY_S
    info = detect(internet=internet)
    if info.internet is not None:
        update_state(internet={**asdict(info.internet), "at": time.time()})
    elif saved:
        info.internet = Internet(**{name: saved.get(name) for name in ("claude_ms", "cloud_ms", "download_mbps")},
                                 error=saved.get("error") or "")
    return info, recommend(info)


def adapt(plan: Plan) -> list[str]:
    """Aplica lo que conviene para este equipo (lo que se decide al arrancar ya lo tomó: sesiones de Claude y
    reconocimiento de voz según la memoria). Devuelve qué cambió."""
    changes = []
    if plan.slow_internet:
        try:
            from .voice import audio

            if audio.STREAM_START_S < SLOW_START_S:
                audio.STREAM_START_S = SLOW_START_S
                changes.append("la voz de la nube arranca con más colchón (internet lento)")
        except ImportError:
            pass
    return changes

def summary_lines(info: System) -> list[tuple[str, str]]:
    """Para mostrar: (qué, cómo está)."""
    gpu = ", ".join(info.gpus[:2]) or "—"
    screen = f"{info.screen[0]}×{info.screen[1]} (escala {int(info.scale * 100)} %)"
    session = {True: "sesión iniciada", False: "sin sesión", None: f"instalado ({info.claude.version or '—'})"}
    claude = ("no instalado" if not info.claude.installed else
              f"{session[info.claude.logged_in]}"
              f"{f' · plan {plan_label(info.claude.plan)}' if info.claude.plan else ''}")
    lines = [("Windows", info.windows), ("Procesador", f"{info.cpu} ({info.threads} hilos)"),
             ("Memoria", f"{info.ram_gb:.0f} GB"), ("Placa de video", gpu), ("Pantalla", screen),
             ("Micrófono", info.default_microphone or "—"),
             ("Micrófono virtual", "instalado" if info.virtual_cable else "no instalado"),
             ("Roblox", info.roblox or "no encontrado"), ("Claude Code", claude),
             ("Lectura del chat", ", ".join(info.ocr_languages[:4]) or "sin idiomas")]
    if info.internet is not None:
        net = info.internet
        parts = []
        if net.claude_ms is not None:
            parts.append(f"Claude {net.claude_ms:.0f} ms")
        if net.cloud_ms is not None:
            parts.append(f"nube {net.cloud_ms:.0f} ms")
        if net.download_mbps is not None:
            parts.append(f"{net.download_mbps:.0f} Mbps".replace(".", ","))
        lines.append(("Internet", " · ".join(parts) or (net.error or "—")))
    return lines
