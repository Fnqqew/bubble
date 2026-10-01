"""Carga de configuración: valores por defecto y %APPDATA%\\Bubble\\config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Bubble" / "config.toml"


def system_locale() -> str:
    """Idioma y país configurados en Windows (por ejemplo, 'es-AR')."""
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
    # "auto" usa el idioma y país de Windows (por ejemplo, es-AR para rioplatense).
    language: str = "auto"
    outgoing_language: str = "auto"
    # Tono de los mensajes enviados: 1 = neutro/formal, 2 = amable, 3 = casual, 4 = gamer, 5 = jerga nativa.
    tone: int = 3
    # Descarga e instala las versiones nuevas automáticamente, sin preguntar, cuando el jugador no está en una partida.
    auto_update: bool = False
    # Idioma de la ventana de Bubble: "auto" usa el de Windows (ver i18n.py).
    ui_language: str = "auto"


@dataclass
class TranslationConfig:
    context_lines: int = 6
    cache_max_words: int = 6
    cache_size: int = 2000
    timeout_s: float = 8.0
    # Aclara entre paréntesis la jerga que no tiene equivalente en el idioma del jugador.
    explain_slang: bool = True
    # Adapta a la variante del jugador los mensajes en su idioma que usan jerga de otro país ("no mames wey" -> "no te
    # puedo creer"). Desactivado: lo que ya está en su idioma no se modifica.
    adapt_slang: bool = False
    # Chat rápido: cada mensaje se traduce primero con el modelo rápido (~1 s) y enseguida lo corrige el modelo
    # principal, más preciso con la jerga (la píldora cambia solo si la versión precisa es distinta). Duplica los
    # pedidos del chat.
    quick_chat: bool = True


@dataclass
class ClaudeConfig:
    model: str = "opus"
    # La voz (la propia y la ajena) y las burbujas usan una sesión independiente, sin razonamiento previo a la
    # respuesta. Con Haiku la voz propia se traduce en ~0,75 s (Opus ~1,9 s) y los subtítulos en ~1 s (Opus ~1,6 s), con
    # calidad casi equivalente. "" usa el mismo modelo que `model`.
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
    # "inline" muestra la traducción sobre cada mensaje del chat; "panel" la muestra en una lista al costado del chat.
    display_mode: str = "inline"
    # Traduce también las burbujas de texto sobre la cabeza de los jugadores.
    translate_bubbles: bool = True
    bubble_interval_s: float = 0.06
    poll_interval_s: float = 0.08  # comprobar si el chat cambió cuesta poco (~2 ms), por eso se hace seguido
    # Rendimiento: "auto" se adapta al procesador de la PC; "alta", "media" y "baja" lo fijan manualmente.
    performance: str = "auto"
    # Captura la pantalla con la GPU, lo que reduce mucho el uso de CPU. Si falla, usa solo la CPU.
    gpu_capture: bool = True
    overlay_seconds: float = 20.0
    username: str = ""
    open_chat_key: str = "/"  # "/" = tecla física del chat de Roblox (sin Shift, en cualquier teclado)
    send_method: str = "type"  # "type": escribe los caracteres (sin Ctrl+V ni usar el portapapeles); "paste"
    ocr_language: str = ""


@dataclass
class VoiceConfig:
    """Voz. Todo el audio se procesa en la PC; Claude solo traduce el texto."""

    # Subtítulos de lo que dicen los demás por el chat de voz (se captura el audio de la PC).
    subtitles: bool = False
    # Voz propia traducida: el jugador mantiene presionada la tecla, habla y la suelta. Sale por el micrófono virtual
    # (VB-Audio Cable).
    speak: bool = False
    push_to_talk: str = "mouse5"
    # Modo de traducción de la voz propia: "boton" (mientras se mantiene presionado push_to_talk) o "directo" (cada
    # frase pronunciada).
    speak_mode: str = "boton"
    # Permite al jugador escuchar su propia voz traducida (en sus auriculares, a menor volumen), además de enviarla a
    # Roblox.
    hear_myself: bool = True
    # Reconocimiento de voz: "auto" elige según el procesador ("base" o "small"); también admite "tiny", "base" y
    # "small".
    model: str = "auto"
    # Sonido de la voz traducida: "femenina" o "masculina", y su velocidad (1 = normal).
    gender: str = "femenina"
    speed: float = 1.0
    # Micrófono real ("" = el predeterminado de Windows): se envía al micrófono virtual junto con la voz traducida.
    mic: str = ""
    pass_my_voice: bool = True
    # Radio de escucha de las voces del juego: "cerca", "normal", "lejos" o "todo" (las voces lejanas suenan más bajo y
    # no se traducen). Lo que suena a ruido y no a una persona hablando nunca se traduce.
    earshot: str = "normal"
    # Traducción en vivo de lo que dicen los demás: la traducción aparece mientras hablan, sin esperar a que terminen.
    # Hace varios pedidos a Claude por frase.
    live_translation: bool = True


@dataclass
class ProConfig:
    """Bubble Pro: el reconocimiento de voz se realiza en la nube (Deepgram), con la cuenta propia del jugador. La
    traducción sigue a cargo de Claude.
    """

    enabled: bool = False
    provider: str = "deepgram"
    # Identificación de quién habla (Voz 1, Voz 2…): por defecto se hace en la PC sin costo; con esta opción la hace la
    # nube (Deepgram la cobra aparte, ~0,12 US$ por hora de voz).
    diarize: bool = False
    # Voces de la nube (Aura-2) para la voz propia traducida y el chat a voz, y su personalidad: "alegre", "canchera" o
    # "tranquila".
    voices: bool = True
    personality: str = "canchera"


@dataclass
class AppearanceConfig:
    """Aspecto de Bubble: la ventana y las traducciones en el juego."""

    theme: str = "oscuro"  # "oscuro" | "claro"
    pill_color: str = "grafito"  # fondo de las traducciones del chat (ver ui/inline.py: PILL_COLORS)
    pill_opacity: float = 0.98
    accent: str = "azul"  # línea de color junto a cada traducción ("ninguno" la quita)
    text_scale: float = 1.0  # tamaño de la letra de las traducciones
    subtitle_size: float = 1.0
    subtitle_position: str = "abajo"  # "abajo" | "arriba"
    subtitle_original: bool = True  # mostrar en tamaño reducido el texto original del hablante
    # Las traducciones aparecen en las capturas y grabaciones del jugador (OBS, Xbox Game Bar, grabadora de Roblox…):
    # Bubble lee la ventana de Roblox directamente (si el equipo lo permite; si no, solo en las capturas con Impr Pant y
    # Win + Shift + S).
    in_screenshots: bool = True


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
    """Orden de prioridad: valores por defecto < config.toml < ajustes guardados desde la ventana."""
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
