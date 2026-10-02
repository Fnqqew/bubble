"""Permite que Bubble funcione en cualquier PC: detecta el equipo, se adapta, reconoce Roblox de la Microsoft Store, se
incluye en las grabaciones cuando es posible y, cuando no, vuelve al comportamiento anterior.
"""

import numpy as np
import pytest
from PIL import Image

from bubble import system
from bubble.geometry import Rect


def pc(**changes) -> system.System:
    """PC con configuración válida, con los cambios que se indiquen."""
    info = system.System(windows="Windows 11", build=26200, python="3.12.8", cpu="Ryzen 5", threads=12, ram_gb=16,
                         gpus=["Radeon"], screen=(1920, 1080), work_area=(1920, 1032), scale=1.25,
                         microphones=["Micrófono"], default_microphone="Micrófono", speakers=["Parlantes"],
                         ocr_languages=["en-US", "es-MX"], roblox="roblox.com",
                         claude=system.Claude(installed=True, version="2.1.0", logged_in=True, plan="max"))
    for name, value in changes.items():
        setattr(info, name, value)
    return info


def levels(info) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in system.recommend(info).advice:
        result.setdefault(item.level, []).append(item.text)
    return result


# ---------------------------------------------------------------- tu equipo
def test_a_good_pc_gets_the_all_clear():
    assert list(levels(pc())) == ["ok"]
    assert system.recommend(pc()).claude_sessions == 3


@pytest.mark.parametrize("ram, sessions", [(4, 1), (5.9, 1), (8, 2), (16, 3), (0, 3)])
def test_claude_sessions_follow_the_memory(ram, sessions):
    assert system.sessions_for(ram) == sessions  # (0: desconocido, se usa el valor habitual)


def test_low_memory_uses_one_session_and_the_light_voice_model():
    plan = system.recommend(pc(ram_gb=4))
    assert plan.claude_sessions == 1 and plan.whisper_ram_limit
    assert any("4 GB" in item.text for item in plan.advice)


@pytest.mark.parametrize("claude, fragment", [
    (system.Claude(), "Falta Claude Code"),
    (system.Claude(installed=True, logged_in=False), "sesión iniciada"),
    (system.Claude(installed=True, logged_in=True, plan="free"), "la gratuita"),
])
def test_claude_problems_are_explained(claude, fragment):
    assert any(fragment in text for text in levels(pc(claude=claude))["problema"])


def test_an_old_claude_code_that_cannot_tell_is_not_a_problem(monkeypatch):
    from bubble import install

    unknown = system.Claude(installed=True, version="1.0.30")  # sin `claude auth status`: se desconoce
    assert "problema" not in levels(pc(claude=unknown))
    assert dict(system.summary_lines(pc(claude=unknown)))["Claude Code"] == "instalado (1.0.30)"
    monkeypatch.setattr(system, "claude_status", lambda: unknown)
    assert install._session_ready()  # no vuelve a abrir «Preparar Bubble» cada vez
    monkeypatch.setattr(system, "claude_status", lambda: system.Claude(installed=True, logged_in=False))
    assert not install._session_ready()


def test_a_paid_plan_is_fine_even_if_claude_code_says_an_old_one():
    for plan in ("pro", "max", "team", "enterprise"):
        assert "problema" not in levels(pc(claude=system.Claude(installed=True, logged_in=True, plan=plan)))
    # (a veces se guarda "pro" aunque el plan sea Max)
    assert system.plan_label("pro") == system.plan_label("max") == "Pro o Max"
    assert system.plan_label("") == "—"


def test_an_api_key_is_a_warning_because_it_would_charge_per_use():
    assert any("ANTHROPIC_API_KEY" in text for text in levels(pc(claude=system.Claude(
        installed=True, logged_in=True, plan="max", api_key=True)))["aviso"])


def test_windows_python_ocr_and_hardware_problems():
    assert any("2004" in text for text in levels(pc(build=18363))["problema"])
    # Windows 10: sin audio por aplicación
    assert any("todo lo que suena" in text for text in levels(pc(build=19045))["aviso"])
    assert any("32 bits" in text for text in levels(pc(python_64bit=False))["problema"])
    assert any("Reconocimiento óptico" in text for text in levels(pc(ocr_languages=[]))["problema"])
    found = levels(pc(microphones=[], roblox="", threads=2, work_area=(1280, 680), scale=1.0))["aviso"]
    assert len(found) == 4  # sin micrófono, sin Roblox, pocos núcleos, pantalla pequeña


def test_internet_speed_decides_the_voice_buffer(monkeypatch):
    from bubble.voice import audio

    fast = system.recommend(pc(internet=system.Internet(claude_ms=20, cloud_ms=190, download_mbps=70)))
    slow = system.recommend(pc(internet=system.Internet(claude_ms=600, cloud_ms=700, download_mbps=1.2)))
    offline = system.recommend(pc(internet=system.Internet(error="Sin conexión a internet")))
    assert not fast.slow_internet and slow.slow_internet
    assert any("No hay conexión" in item.text for item in offline.advice)
    monkeypatch.setattr(audio, "STREAM_START_S", 0.35)
    assert system.adapt(fast) == [] and audio.STREAM_START_S == 0.35
    assert system.adapt(slow) and audio.STREAM_START_S == system.SLOW_START_S


def test_summary_lines_read_well():
    lines = dict(system.summary_lines(pc(internet=system.Internet(claude_ms=20, cloud_ms=195, download_mbps=72.4))))
    assert lines["Memoria"] == "16 GB"
    assert lines["Pantalla"] == "1920×1080 (escala 125 %)"
    assert lines["Claude Code"] == "sesión iniciada · plan Pro o Max"
    assert lines["Internet"] == "Claude 20 ms · nube 195 ms · 72 Mbps"


def test_internet_is_measured_once_a_day(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    measured = []

    def detect(internet=False):
        info = pc()
        if internet:
            measured.append(1)
            info.internet = system.Internet(claude_ms=30, cloud_ms=200, download_mbps=50)
        return info

    monkeypatch.setattr(system, "detect", detect)
    first, _ = system.check()
    second, _ = system.check()
    assert len(measured) == 1  # la segunda vez usa la medición guardada
    assert second.internet.claude_ms == 30 and second.internet.download_mbps == 50
    system.check(internet=True)
    assert len(measured) == 2  # «Medir internet»: se mide de todas formas


def test_detects_this_pc_for_real():
    info = system.detect()
    assert info.build and info.threads and info.ram_gb > 0 and info.screen[0] > 0
    assert info.scale >= 1.0


# ---------------------------------------------------------------- Roblox de la Microsoft Store
def test_roblox_from_the_store_is_recognized(monkeypatch):
    from bubble import win32

    paths = {1: r"C:\Program Files\Roblox\Versions\version-1\RobloxPlayerBeta.exe",
             2: r"C:\Program Files\WindowsApps\ROBLOXCORPORATION.ROBLOX_2.6_x64\Windows10Universal.exe",
             3: r"C:\Program Files\WindowsApps\Microsoft.Calculator\Windows10Universal.exe",
             4: r"C:\Windows\System32\notepad.exe"}
    monkeypatch.setattr(win32, "_process_path", lambda pid: paths.get(pid, ""))
    assert win32.is_roblox_process(1) and win32.is_roblox_process(2)
    assert not win32.is_roblox_process(3)  # otra aplicación de la Store: no es Roblox
    assert not win32.is_roblox_process(4) and not win32.is_roblox_process(99)


# ---------------------------------------------------------------- que salga en las grabaciones
def test_window_images_are_compared_with_the_screen():
    from bubble.capture.window_capture import looks_blank, similar

    rng = np.random.default_rng(1)
    game = Image.fromarray(rng.integers(0, 255, (120, 200, 3), dtype=np.uint8))
    noisy = Image.fromarray(np.clip(np.asarray(game, dtype=np.int16) + 3, 0, 255).astype(np.uint8))
    assert similar(game, noisy)
    assert not similar(game, Image.new("RGB", game.size))  # Windows devolvió una imagen negra: no es válida
    assert not similar(game, game.resize((100, 60)))
    assert looks_blank(np.zeros((50, 50, 4), dtype=np.uint8))
    assert not looks_blank(np.asarray(game.convert("RGBA")))


class FakeWindow:
    def __init__(self, image):
        self.image = image

    def grab(self, rect):
        return self.image


@pytest.fixture
def window_mode(monkeypatch):
    from bubble.capture import screen

    game = Image.fromarray(np.random.default_rng(2).integers(0, 255, (40, 60, 3), dtype=np.uint8))
    monkeypatch.setattr(screen, "_grab_screen", lambda rect: game.copy())
    fake = FakeWindow(game.copy())
    monkeypatch.setattr(screen, "_window", {"wanted": False, "active": False, "capture": fake, "probed_at": 0.0,
                                            "fails": 0, "on_change": None})
    changes = []
    screen.set_window_capture(True, changes.append)
    return screen, fake, changes, Rect(0, 0, 60, 40)


def test_reads_the_roblox_window_when_it_matches_the_screen(window_mode):
    screen, _fake, changes, rect = window_mode
    screen.grab(rect)
    assert screen.window_mode() and changes == [True]  # las traducciones se incluyen en las grabaciones
    assert screen.capture_backend() == "ventana"


def test_does_not_switch_when_windows_gives_a_black_window(window_mode):
    screen, fake, changes, rect = window_mode
    fake.image = Image.new("RGB", (60, 40))
    screen.grab(rect)
    assert not screen.window_mode() and changes == []


def test_goes_back_to_the_screen_if_the_window_stops_working(window_mode):
    screen, fake, changes, rect = window_mode
    screen.grab(rect)
    fake.image = None  # Roblox minimizado o Windows dejó de generar la imagen
    for _ in range(screen.FAILS_TO_GIVE_UP - 1):
        assert screen.grab(rect).getbbox() is None  # negro por un instante (puede ser un parpadeo)
        assert screen.window_mode()
    image = screen.grab(rect)
    assert not screen.window_mode() and changes == [True, False]
    assert image.getbbox() is not None  # y se sigue leyendo la pantalla


def test_turning_it_off_hides_the_translations_again(window_mode):
    screen, _fake, changes, rect = window_mode
    screen.grab(rect)
    screen.set_window_capture(False)
    assert not screen.window_mode() and changes == [True, False]


# ---------------------------------------------------------------- la ventana en cualquier pantalla
@pytest.mark.parametrize("dpi, work", [(96, (0, 0, 1366, 728)), (120, (0, 0, 1920, 1032)),
                                       (144, (0, 0, 1920, 1032)), (192, (0, 0, 2560, 1400)),
                                       (96, (1920, 0, 3200, 984))])
def test_the_window_always_fits_the_screen(dpi, work):
    from bubble.ui.main_window import window_geometry

    (width, height, x, y), (min_w, min_h) = window_geometry(dpi, work)
    left, top, right, bottom = work
    assert left <= x and x + width <= right and top <= y and y + height <= bottom
    assert min_w <= width and min_h <= height


def test_the_window_keeps_its_design_at_125_percent():
    from bubble.ui.main_window import window_geometry

    (width, height, _x, _y), _ = window_geometry(120, (0, 0, 1920, 1032))
    assert (width, height) == (600, 820)
    (width, height, _x, _y), _ = window_geometry(144, (0, 0, 2560, 1400))
    assert (width, height) == (720, 984)  # al 150 %, tamaño mayor para que no quede pequeña


# ---------------------------------------------------------------- instalar
def test_install_checks_windows_parts_and_the_claude_session():
    from bubble import install

    steps = {step.key: step for step in install.steps()}
    assert list(steps)[0] == "windows"  # sin Visual C++ no carga el módulo de voz: va primero
    assert steps["sesion"].after == ["claude"] and steps["sesion"].action
    assert not install.automatic(steps["windows"])  # pide permiso: nunca se instala en segundo plano
    assert install._runtime_ready()  # (esta PC lo tiene)


def test_ocr_uses_another_installed_language_if_yours_is_missing():
    from bubble.capture.ocr import WindowsOcr

    assert WindowsOcr("xx-XX").language  # sin lector de texto no se podía leer el chat


# ---------------------------------------------------------------- si Windows bloquea las voces de Piper
class FakeWindowsVoices:
    def __init__(self):
        self.said = []

    def has(self, language):
        return language.split("-")[0] in ("es", "en")

    def voice_for(self, language, gender="femenina"):
        from types import SimpleNamespace

        return SimpleNamespace(name=f"Microsoft {language}", gender=gender) if self.has(language) else None

    def synthesize(self, text, language, gender="femenina", speed=1.0, style="", name=""):
        self.said.append((text, language, gender, style))
        voice = (0.3 * np.sin(2 * np.pi * 150 * np.arange(11025) / 22050)).astype(np.float32)  # (medio segundo)
        return (voice, 22050) if self.has(language) else None


def test_when_windows_blocks_piper_the_windows_voices_are_used(monkeypatch):
    import bubble.voice.windows_voices
    from bubble.voice import tts

    monkeypatch.setitem(tts._piper_state, "error", "Una directiva de Control de aplicaciones bloqueó este archivo")
    fake = FakeWindowsVoices()
    monkeypatch.setattr(bubble.voice.windows_voices, "WindowsVoices", lambda: fake)
    voices = tts.Voices()
    voices.gender = "masculina"
    speech = voices.synthesize("esperame en la torre", "es-AR", style="exclaim")
    assert speech.sample_rate == 22050 and fake.said == [("esperame en la torre", "es-AR", "masculina", "exclaim")]
    assert voices.is_downloaded("en") and voices.prepare("es") and voices.download("en")  # nada que descargar
    assert voices.synthesize("olá", "pt") is None and not voices.is_downloaded("pt")  # (esa voz no existe en Windows)


def test_your_pc_says_when_windows_blocks_the_voices():
    found = levels(pc(voices_blocked=True))["aviso"]
    assert any("Control inteligente de aplicaciones" in text for text in found)
    assert dict(system.summary_lines(pc(voices_blocked=True)))["Voces sintéticas"].startswith("las de Windows")


def test_windows_voices_really_speak():
    from bubble.voice.windows_voices import WindowsVoices

    voices = WindowsVoices()
    if not voices.has("en") and not voices.has("es"):
        pytest.skip("esta PC no tiene voces de Windows")
    language = "es-AR" if voices.has("es") else "en"
    audio, rate = voices.synthesize("¡Esperame en la torre!", language, "femenina", style="exclaim")
    assert rate == 22050 and len(audio) > rate * 0.5 and 0.05 < float(np.abs(audio).max()) <= 1.0
    if voices.has("es"):
        chosen = voices.voice_for("es-AR", "femenina")
        assert chosen.language in ("es-MX", "es-AR", "es-US") or not any(
            v.language.lower() == "es-mx" for v in voices.voices())  # español latino antes que el de España


def test_windows_voices_load_on_any_kind_of_thread():
    """La ventana (Tk) inicializa COM de hilo único y el audio, de múltiples hilos; las voces de Windows funcionan con
    ambos modelos.
    """
    import subprocess
    import sys

    code = ("import ctypes, threading\n"
            "ctypes.windll.ole32.CoInitializeEx(None, 2)\n"  # como el hilo de la ventana
            "from bubble.voice.windows_voices import WindowsVoices\n"
            "w = WindowsVoices(); w.voices()\n"
            "out = []\n"
            "def run():\n"
            "    ctypes.windll.ole32.CoInitializeEx(None, 0)\n"  # como el hilo de audio
            "    out.append(len(w.voices()))\n"
            "t = threading.Thread(target=run); t.start(); t.join()\n"
            "print('ok', out)\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.startswith("ok"), done.stderr[-400:]
