"""Carga de configuración: valores por defecto + %APPDATA%\\Bubble\\config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Bubble" / "config.toml"


def system_locale() -> str:
    """Idioma y país configurados en Windows (ej. 'es-AR')."""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return "en-US"


@dataclass
class UserConfig:
    # "auto" = el idioma y país de Windows (ej. es-AR para rioplatense).
    language: str = "auto"
    outgoing_language: str = "auto"
    # Tono de lo que enviás: 1 = neutro/formal, 2 = amable, 3 = casual, 4 = gamer, 5 = jerga nativa.
    tone: int = 3


@dataclass
class TranslationConfig:
    context_lines: int = 6
    cache_max_words: int = 6
    cache_size: int = 2000
    timeout_s: float = 8.0
    # Aclarar entre paréntesis la jerga que no tiene equivalente en tu idioma.
    explain_slang: bool = True
    # Pasar a tu variante los mensajes en tu idioma con jerga de otro país ("no mames wey" -> "no te puedo creer").
    # Apagado: lo que ya está en tu idioma no se toca.
    adapt_slang: bool = False


@dataclass
class ClaudeConfig:
    model: str = "opus"
    # La voz (lo que decís y lo que te dicen) y las burbujas van por su propia sesión, sin pensar antes de responder.
    # Con Haiku tu voz se traduce en ~0,75 s (con Opus ~1,9 s) y los subtítulos en ~1 s (Opus ~1,6 s), con casi la
    # misma calidad (medido). "" = el mismo modelo que `model`.
    voice_model: str = "haiku"
    effort: str = "low"
    pool_size: int = 3
    session_max_turns: int = 15
    cli_path: str = ""


@dataclass
class RobloxConfig:
    # Atajo para escribir en Roblox: una tecla ("°", "F8", "ctrl+t") o un botón del mouse ("mouse4").
    hotkey: str = "°"
    read_chat: bool = True
    # "inline" = traducción encima de cada mensaje del chat; "panel" = lista al costado del chat.
    display_mode: str = "inline"
    # Traducir también las burbujas de texto sobre la cabeza de los jugadores.
    translate_bubbles: bool = True
    bubble_interval_s: float = 0.06
    poll_interval_s: float = 0.08  # ver si el chat cambió es barato (~2 ms): se mira seguido
    # Rendimiento: "auto" se adapta al procesador de tu PC; "alta", "media" o "baja" lo fijan a mano.
    performance: str = "auto"
    # Capturar la pantalla con la placa de video (mucho menos uso de CPU). Si falla, se usa la CPU sola.
    gpu_capture: bool = True
    overlay_seconds: float = 20.0
    username: str = ""
    open_chat_key: str = "/"  # "/" = la tecla física del chat de Roblox (sin Shift, en cualquier teclado)
    send_method: str = "type"  # "type": escribe los caracteres (sin Ctrl+V ni tocar el portapapeles); "paste"
    ocr_language: str = ""


@dataclass
class VoiceConfig:
    """Voz. Todo el audio se procesa en tu PC; Claude solo traduce el texto."""

    # Subtítulos de lo que te dicen por el chat de voz (se escucha el audio de la PC).
    subtitles: bool = False
    # Tu voz traducida: mantené apretada la tecla, hablá y soltala. Sale por el micrófono virtual (VB-Audio Cable).
    speak: bool = False
    push_to_talk: str = "mouse5"
    # Cómo se traduce tu voz: "boton" (mientras mantenés apretado push_to_talk) o "directo" (cada frase que decís).
    speak_mode: str = "boton"
    # Escuchar vos también tu voz traducida (en tus auriculares, más bajo), además de mandarla a Roblox.
    hear_myself: bool = True
    # Reconocimiento de voz: "auto" elige según tu procesador ("base" o "small"); también "tiny", "base", "small".
    model: str = "auto"
    # Cómo suena tu voz traducida: "femenina" o "masculina", y su velocidad (1 = normal).
    gender: str = "femenina"
    speed: float = 1.0
    # Tu micrófono real ("" = el predeterminado de Windows): pasa al micrófono virtual junto con la voz traducida.
    mic: str = ""
    pass_my_voice: bool = True
    # Radio de escucha de las voces del juego: "cerca", "normal", "lejos" o "todo" (las lejanas suenan más bajo y no se
    # traducen). Lo que suena a ruido y no a alguien hablando no se traduce nunca.
    earshot: str = "normal"


@dataclass
class ProConfig:
    """Bubble Pro: la voz se entiende en la nube (Deepgram), con tu propia cuenta. La traducción sigue con Claude."""

    enabled: bool = False
    provider: str = "deepgram"
    # Quién habla (Voz 1, Voz 2…): lo hace tu PC gratis; con esto lo hace la nube (Deepgram lo cobra aparte, ~0,12 US$
    # por hora de voz).
    diarize: bool = False
    # Las voces de la nube para tu voz traducida y el chat a voz (Aura-2), y su personalidad: "alegre", "canchera" o
    # "tranquila".
    voices: bool = True
    personality: str = "canchera"


@dataclass
class AppearanceConfig:
    """Cómo se ve Bubble: la ventana y las traducciones en el juego."""

    theme: str = "oscuro"  # "oscuro" | "claro"
    pill_color: str = "grafito"  # fondo de las traducciones del chat (ver ui/inline.py: PILL_COLORS)
    pill_opacity: float = 0.98
    accent: str = "azul"  # la rayita de color de cada traducción ("ninguno" para sacarla)
    text_scale: float = 1.0  # tamaño de la letra de las traducciones
    subtitle_size: float = 1.0
    subtitle_position: str = "abajo"  # "abajo" | "arriba"
    subtitle_original: bool = True  # mostrar chiquito lo que dijeron en su idioma
    in_screenshots: bool = True  # las traducciones salen en tus capturas (Impr Pant, Win + Shift + S)


@dataclass
class Config:
    user: UserConfig = field(default_factory=UserConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    roblox: RobloxConfig = field(default_factory=RobloxConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    pro: ProConfig = field(default_factory=ProConfig)


def _merge(section, values: dict) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key in known:
            setattr(section, key, value)


SECTIONS = ("user", "translation", "claude", "roblox", "voice", "appearance", "pro")


def save_setting(section: str, key: str, value) -> None:
    """Guarda un ajuste hecho desde la ventana. Tiene prioridad sobre config.toml."""
    from .state import load_state, update_state

    settings = load_state().get("settings", {})
    settings.setdefault(section, {})[key] = value
    update_state(settings=settings)


def load_config(path: Path | None = None) -> Config:
    """Valores por defecto < config.toml < ajustes guardados desde la ventana."""
    from .state import load_state

    config = Config()
    path = path or default_config_path()
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for name in SECTIONS:
            _merge(getattr(config, name), data.get(name, {}))
    saved = load_state().get("settings", {})
    for name in SECTIONS:
        _merge(getattr(config, name), saved.get(name, {}))
    if config.user.language in ("", "auto"):
        from .translate.languages import LANGUAGES

        locale = system_locale()
        config.user.language = locale if locale.split("-")[0].lower() in LANGUAGES else "en-US"
    return config
