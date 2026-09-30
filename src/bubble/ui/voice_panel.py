"""La voz: subtítulos de lo que dicen los demás jugadores y la voz del jugador traducida para que los demás la escuchen
(página «Voz» de la ventana).

Todo el audio se procesa en la PC (Whisper para reconocer, Piper para hablar); Claude solo traduce el texto. Para que
los demás escuchen al jugador, Bubble habla por un micrófono virtual, como Soundpad (ver voice/bridge.py).
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import win32
from ..config import save_setting
from ..state import load_state, update_state
from . import widgets
from .subtitles import SubtitleView, speaker_name

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .main_window import BubbleWindow

MODES = {"boton": "Con un botón", "directo": "Directo, sin botón"}
GENDERS = {"femenina": "Femenina", "masculina": "Masculina"}
SAMPLES = {
    "en": "Hi! This is how I'm going to sound.", "pt": "Oi! É assim que eu vou soar.",
    "es": "¡Hola! Así va a sonar mi voz.", "fr": "Salut ! Voilà comment je vais sonner.",
    "de": "Hallo! So werde ich klingen.", "it": "Ciao! Ecco come suonerò.", "ru": "Привет! Вот так я буду звучать.",
    "hi": "नमस्ते! मेरी आवाज़ ऐसी सुनाई देगी।", "pl": "Cześć! Tak będę brzmieć.", "nl": "Hoi! Zo ga ik klinken.",
    "tr": "Merhaba! Sesim böyle olacak.", "id": "Halo! Beginilah suaraku.", "zh": "你好！我的声音听起来是这样的。",
    "ko": "안녕하세요! 제 목소리는 이렇게 들려요.", "ja": "こんにちは！こんな声になります。",
    "vi": "Xin chào! Giọng của tôi sẽ như thế này.", "th": "สวัสดี! เสียงของฉันจะเป็นแบบนี้", "ar": "مرحبا! هكذا سيبدو صوتي.",
}
NO_VOICE_PACK = 'Falta instalar la parte de voz: .venv\\Scripts\\python.exe -m pip install -e ".[voz]"'


WARM_DELAY_MS = 600  # con Tab: la voz se prepara cuando se deja de cambiar de idioma
OUT_OF_GAME_S = 3.0  # fuera del juego más que esto, las voces del juego no se escuchan


class VoicePanel:
    def __init__(self, app: BubbleWindow) -> None:
        self.app = app
        self.config = app.config.voice
        self.models = None  # (whisper final, whisper rápido o None, voces conocidas)
        self._kind = ""  # "nube" o "pc": con qué se armaron la escucha y la voz (si cambia, se rearman)
        self._warm_after = None  # preparar la voz del idioma elegido (con Tab) poco después
        self._cloud_warned = 0.0
        self._cloud_voices = None  # voces de la nube (Bubble Pro), con prioridad sobre las de la PC
        self._out_of_game = 0.0  # desde cuándo el jugador no está en el juego (pausa la escucha)
        from ..voice.hearing import Earshot

        self.earshot = Earshot(self.config.earshot)  # radio de escucha (voice/hearing.py): se conserva entre rearmados
        self.voices = None
        self.out = None  # la voz sintética (ver voice/pipelines.py)
        self.bridge = None  # micrófono real pasando al virtual (voice/bridge.py)
        self.listener = None
        self.speaker = None  # voz del jugador: con tecla o directa
        self.subtitles = SubtitleView()
        self.board = None  # frases a la vista (ver voice/captions.py)
        self._preparing = False
        self._samples: dict[tuple, object] = {}  # frases de prueba ya pronunciadas
        self.subtitles_var = tk.BooleanVar(value=self.config.subtitles)
        self.speak_var = tk.BooleanVar(value=self.config.speak)
        self.mode_var = tk.StringVar(value=self.config.speak_mode if self.config.speak_mode in MODES else "boton")
        self.hear_var = tk.BooleanVar(value=self.config.hear_myself)
        self.gender_var = tk.StringVar(value=self.config.gender if self.config.gender in GENDERS else "femenina")
        self.speed_var = tk.DoubleVar(value=self.config.speed)
        self.pass_var = tk.BooleanVar(value=self.config.pass_my_voice)
        self.earshot_var = tk.StringVar(value=self.earshot.radius)
        self._cable_ok = True  # hasta revisar los dispositivos
        self._soundpad = False  # el micrófono de Windows es el virtual (ver voice/devices.py)
        # El micrófono virtual pasa a ser el de Windows apenas arranca el puente; hasta entonces no se corrige la
        # configuración de Windows, porque la revisión de dispositivos lo quitaba y un segundo después volvía a ponerse,
        # y Roblox detectaba el cambio.
        self._soundpad_planned = True
        self._bridge_lock = threading.Lock()
        self.status = None
        self.lang_box = None
        from ..voice.profile import VoiceProfile

        self.profile = VoiceProfile()  # lo que se aprende de cómo habla el jugador (voice/profile.py)
        self._tick()

    # ------------------------------------------------------------ página «Voz»
    def build_page(self, page) -> None:
        box = widgets.card(page, "Lo que te dicen")
        widgets.switch_row(box, "listen", "Subtítulos de voz", "Ves quién habla y qué dice, en tu idioma. Lo "
                                                               "que ya está en tu idioma no lo subtitulo.", self.subtitles_var, self._toggle_subtitles)
        from ..voice.hearing import RADIUS_NAMES

        self.earshot_box = ttk.Frame(box)  # (se difumina con los subtítulos apagados: ver _update_locks)
        self.earshot_box.pack(fill="x")
        row = widgets.label_row(self.earshot_box, "Radio de escucha", pady=(12, 2))
        widgets.segmented(row, self.earshot_var, RADIUS_NAMES, self._change_earshot).pack(side="right")
        widgets.muted(self.earshot_box, "Los que están lejos se escuchan más bajo. Con «Cerca» traduzco solo a "
                                        "los que tenés al lado. La música, las explosiones y las risas no se "
                                        "traducen.")

        box = widgets.card(page, "Tu voz para los demás")
        widgets.switch_row(box, "mic", "Traducir mi voz", "Hablás en tu idioma y te escuchan en el suyo.",
                           self.speak_var, self._toggle_speak)
        from .main_window import AUTO_CHOICE, LANG_CHOICES, _choice, _code

        self.speak_rows = ttk.Frame(box)  # (con la voz del jugador apagada no corresponden: se difuminan)
        self.speak_rows.pack(fill="x")
        row = widgets.label_row(self.speak_rows, "Te escuchan en", pady=(12, 2))
        self.lang_box = ttk.Combobox(row, values=[AUTO_CHOICE, *LANG_CHOICES], state="readonly", width=30)
        self.lang_box.set(_choice(self.app.config.user.outgoing_language))
        self.lang_box.bind("<<ComboboxSelected>>", lambda _e: self.app._set_outgoing(_code(self.lang_box.get())))
        self.lang_box.pack(side="right")
        row = widgets.label_row(self.speak_rows, "Cómo", pady=(8, 2))
        widgets.segmented(row, self.mode_var, MODES, self._change_mode).pack(side="right")
        self.button_rows = ttk.Frame(box)  # (en modo directo no hay botón: se difumina)
        self.button_rows.pack(fill="x")
        row = widgets.label_row(self.button_rows, "Botón para hablar")
        ttk.Button(row, text="Cambiar", command=self._change_key).pack(side="right")
        self.ptt_label = ttk.Label(row, text=win32.describe_binding(self.config.push_to_talk),
                                   font="SunValleyBodyStrongFont")
        self.ptt_label.pack(side="right", padx=10)
        widgets.muted(box, "Tocá el botón y hablá. Cuando terminás, lo traduzco y lo digo (también podés mantenerlo "
                           "presionado mientras hablás). En modo directo no hace falta tocar nada: te escucho "
                           "mientras estás en Roblox.")
        self.cable_warning = ttk.Label(box, text="", foreground=widgets.palette()["warn"], wraplength=440,
                                       justify="left")
        self.cable_warning.pack(anchor="w", pady=(8, 0))

        box = widgets.card(page, "Cómo suena")
        row = widgets.label_row(box, "Voz")
        widgets.segmented(row, self.gender_var, GENDERS, self._change_voice).pack(side="right")
        row = widgets.label_row(box, "Velocidad")
        self.speed_text = ttk.Label(row, text=self._speed_label(), width=10, anchor="e")
        self.speed_text.pack(side="right")
        ttk.Scale(row, from_=0.8, to=1.3, variable=self.speed_var, command=lambda _v: self._change_speed(),
                  length=170).pack(side="right", padx=8)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 2))
        ttk.Checkbutton(row, text="Escucharla yo también", variable=self.hear_var, command=self._toggle_hear,
                        style="Switch.TCheckbutton").pack(side="left")
        ttk.Button(row, text="Probar voz", command=self._try_voice).pack(side="right")

        box = widgets.card(page, "Micrófono", "Como Soundpad: hablo por un micrófono virtual que reúne tu voz y la "
                                              "traducida. Si abrís Bubble antes que Roblox, este lo detecta "
                                              "automáticamente.")
        row = widgets.label_row(box, "Tu micrófono")
        self.mic_box = ttk.Combobox(row, state="readonly", width=30, values=["Buscando…"])
        self.mic_box.set(self.config.mic or "El predeterminado de Windows")
        self.mic_box.bind("<<ComboboxSelected>>", self._change_mic)
        self.mic_box.pack(side="right")
        row = widgets.label_row(box, "Micrófono virtual")
        self.cable_button = ttk.Button(row, text="Instalar (gratis)", command=self._install_cable)
        self.cable_label = ttk.Label(row, text="Revisando…", foreground=widgets.palette()["muted"])
        self.cable_label.pack(side="right")
        self.pass_box = ttk.Frame(box)  # (sin micrófono virtual no hay destino: se difumina)
        self.pass_box.pack(fill="x")
        ttk.Checkbutton(self.pass_box, text="Pasar también mi voz real", variable=self.pass_var,
                        command=self._toggle_pass, style="Switch.TCheckbutton").pack(anchor="w", pady=(10, 0))
        self.cable_help = widgets.muted(box, "")
        self.windows_row = ttk.Frame(box)
        self.windows_warning = ttk.Label(self.windows_row, text="", foreground=widgets.palette()["warn"],
                                         wraplength=330, justify="left")
        self.windows_warning.pack(side="left", fill="x", expand=True)
        ttk.Button(self.windows_row, text="Arreglar Windows", command=self.fix_windows).pack(side="right")
        self.status = widgets.muted(page, "", pady=(0, 8))
        self._update_locks(animate=False)
        threading.Thread(target=self._scan_devices, name="bubble-dispositivos", daemon=True).start()

    def _update_locks(self, animate: bool = True) -> None:
        """Lo que no corresponde en el estado actual se difumina y queda deshabilitado, para evitar pruebas a
        medias: el radio de escucha sin subtítulos, el idioma y el modo sin la voz del jugador, el botón en modo
        directo, pasar la voz real sin micrófono virtual, y la apariencia de los subtítulos (en Ajustes) sin
        subtítulos.
        """
        if not hasattr(self, "earshot_box"):
            return
        speaking = self.speak_var.get()
        widgets.dim(self.earshot_box, not self.subtitles_var.get(), animate)
        widgets.dim(self.speak_rows, not speaking, animate)
        widgets.dim(self.button_rows, not speaking or self.mode_var.get() == "directo", animate)
        widgets.dim(self.pass_box, not self._cable_ok, animate)
        subtitles_look = getattr(self.app, "subs_box", None)
        if subtitles_look is not None:
            widgets.dim(subtitles_look, not self.subtitles_var.get(), animate)

    def _change_earshot(self) -> None:
        self.config.earshot = self.earshot.radius = self.earshot_var.get()
        save_setting("voice", "earshot", self.config.earshot)

    def _speed_label(self) -> str:
        speed = self.speed_var.get()
        return "Normal" if abs(speed - 1.0) < 0.04 else ("Más lenta" if speed < 1 else "Más rápida")

    def _scan_devices(self) -> None:
        try:
            from ..voice import bridge

            from ..voice.devices import restore_real_defaults, wrong_defaults

            wrong = wrong_defaults()
            if self._soundpad or self._soundpad_planned:
                wrong = [item for item in wrong if item != "micrófono"]  # el micrófono virtual es intencional
            if wrong and not self._soundpad:
                # El instalador del micrófono virtual lo dejó como predeterminado (o Bubble se cerró de forma abrupta):
                # se restauran los dispositivos reales del jugador automáticamente.
                fixed = restore_real_defaults()
                if fixed:
                    self._set_status("Dejé Windows como estaba: " + " y ".join(fixed) + ". El micrófono virtual "
                                     "lo uso solo mientras traduzco tu voz.")
                wrong = wrong_defaults()
            mics, cable = bridge.microphones(), bridge.cable_input() is not None
        except Exception:  # noqa: BLE001 - sin la parte de voz instalada
            mics, cable, wrong = [], False, []
        self.app.events.put(("voice_devices", (mics, cable, wrong)))

    def show_devices(self, mics: list[str], cable: bool, wrong: list[str] | tuple = ()) -> None:
        """(hilo de la ventana) Micrófonos encontrados, si hay micrófono virtual y si Windows quedó usándolo."""
        self.mic_box.configure(values=["El predeterminado de Windows", *mics])
        colors = widgets.palette()
        if cable != self._cable_ok:
            self._cable_ok = cable
            self._update_locks()
        self.cable_warning.configure(text="" if cable else ("Falta el micrófono virtual, y sin él los demás no "
                                                            "escuchan tu voz traducida. Instalalo más abajo; demora "
                                                            "un minuto."))
        if wrong:
            self.windows_warning.configure(text=f"Al instalarse, el micrófono virtual quedó como {' y '.join(wrong)} de "
                                                "Windows, y Discord y los demás no te escuchan.")
            self.windows_row.pack(fill="x", pady=(8, 0))
        else:
            self.windows_row.pack_forget()
        if cable:
            self.cable_label.configure(text="Instalado ✓", foreground=colors["good"])
            self.cable_button.pack_forget()
            self.cable_help.configure(text="Ya está: no hay que configurar nada. Mientras Bubble está abierto, los "
                                           "demás te escuchan por el micrófono virtual, con tu voz y la traducida. "
                                           "Cuando lo cerrás, vuelve el tuyo. Abrí Bubble antes que Roblox y recordá "
                                           "tener el micrófono activado en el juego.")
        else:
            self.cable_label.configure(text="No instalado", foreground=colors["warn"])
            self.cable_button.pack(side="right", padx=(0, 10))
            self.cable_help.configure(text="Sin el micrófono virtual, tu voz traducida suena solo en tus "
                                           "auriculares. Instalarlo tarda un minuto.")

    # ------------------------------------------------------------ arranque (cuando la app ya tiene Claude listo)
    def early_start(self) -> None:
        """Al abrir Bubble (sin esperar a Claude), el micrófono virtual pasa a ser el de Windows. Roblox elige su
        micrófono al abrirse, así que debe estar listo antes. También se vigila cuál usa Roblox, para avisar al
        jugador.
        """
        threading.Thread(target=self._start_soundpad, name="bubble-soundpad", daemon=True).start()
        threading.Thread(target=self._watch_roblox_mic, name="bubble-microfono-roblox", daemon=True).start()

    def start(self) -> None:
        # Como Soundpad: con el micrófono virtual instalado, mientras Bubble está abierto la voz del jugador pasa por él
        # y es el micrófono de Windows, sin importar si se usa el botón, el modo directo o Ctrl+Enter.
        threading.Thread(target=self._start_soundpad, name="bubble-soundpad", daemon=True).start()
        if self.app.translator is not None:
            self.app.translator.examples_for = self.profile.examples  # estilo de voz elegido (página Pruebas)
            self.app.translator.vocabulary_for = self._vocabulary_for_claude  # vocabulario del jugador, para Claude
        if not (self.config.subtitles or self.config.speak):
            return
        if load_state().get("voice_loading"):
            # La última vez Bubble se cerró mientras cargaba la voz: esta vez no se carga automáticamente, para que la
            # ventana abra.
            update_state(voice_loading=False)
            self._set_status("La última vez Bubble se cerró mientras preparaba la voz, así que la dejé en pausa. "
                             "Para probar de nuevo, apagá y volvé a encender el interruptor.")
            return
        self._prepare(self._apply)

    def stop(self) -> None:
        for part in (self.listener, self.speaker, self.bridge):
            if part:
                part.stop()
        self._route_mic(False, wait=True)  # al cerrar Bubble, Windows vuelve al micrófono real
        if self._preparing:
            update_state(voice_loading=False)  # Bubble se cerró a mitad de la descarga: no es un error

    def pause(self) -> None:
        """Se cerró Roblox o se refrescó: se apagan la escucha y la voz del jugador, y se olvida la partida (las
        voces vuelven a numerarse desde 1). Los modelos quedan cargados, por lo que retomar con `start()` es
        rápido. El micrófono virtual se mantiene: si Windows volviera al micrófono real, el próximo Roblox
        arrancaría con ese y no escucharía al jugador.
        """
        for part in (self.listener, self.speaker):
            if part:
                part.stop()
        self.listener = self.speaker = self.out = self.board = None
        if self.models is not None and self.models[2] is not None:
            self.models[2].voices.clear()

    def _set_status(self, text: str) -> None:
        self.app.events.put(("voice_status", text))

    def _prepare(self, then) -> None:
        """Carga (y descarga la primera vez) el reconocimiento de voz, sin bloquear la ventana."""
        if self.models is not None:
            then()
            return
        if self._preparing:
            return
        self._preparing = True

        def work() -> None:
            try:
                from ..voice.asr import FastWhisper, LazyWhisper, pick_models
                from ..voice.speakers import SpeakerTracker

                quick, final = pick_models()
                if self.config.model not in ("", "auto"):
                    final = self.config.model
                if self._cloud_key():
                    # Bubble Pro: la voz se reconoce en la nube; el reconocimiento de la PC queda de respaldo y se carga
                    # solo si hace falta (arranca al instante y no ocupa memoria).
                    models = (LazyWhisper(final, 4), LazyWhisper(quick, 2) if quick else None, SpeakerTracker())
                else:
                    self._set_status("Preparando la voz… La primera vez se descarga (puede pesar 500 MB) y queda en "
                                     "tu PC.")
                    update_state(voice_loading=True)  # si el proceso se cae acá, el próximo arranque no la carga sola
                    models = (FastWhisper(final, 4), FastWhisper(quick, 2) if quick else None, SpeakerTracker())
                    update_state(voice_loading=False)
                self._ensure_out()
                self.models = models
                self._set_status("")
                self.app.events.put(("voice_ready", then))
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                update_state(voice_loading=False)
                self._set_status(f"No se pudo preparar la voz: {exc}")
            finally:
                self._preparing = False

        threading.Thread(target=work, name="bubble-voz-prepara", daemon=True).start()

    def _ensure_bridge(self):
        """El puente con el micrófono real (si hay micrófono virtual): la voz real pasa al virtual en vivo."""
        with self._bridge_lock:
            if self.bridge is not None and self.bridge.running:
                return self.bridge
            from ..voice import bridge

            if bridge.cable_input() is None:
                return None
            self.bridge = bridge.MicBridge(self.config.mic)
            self.bridge.enabled = self.config.pass_my_voice
            self.bridge.start()
            return self.bridge

    def _start_soundpad(self) -> None:
        try:
            if self._ensure_bridge() is not None:
                self._route_mic(True)
                return
        except Exception:  # noqa: BLE001 - sin la parte de voz instalada
            log.warning("No se pudo preparar el micrófono virtual", exc_info=True)
        self._soundpad_planned = False  # sin puente no hay modo Soundpad: Windows debe quedar con el micrófono real
        self._scan_devices()

    def _ensure_out(self):
        """La voz sintética y, si hay micrófono virtual, el puente con el micrófono real."""
        if self.out is not None:
            return self.out
        from ..voice.pipelines import VoiceOut

        self._ensure_bridge()
        self.out = VoiceOut(self._voices_now(), self.config.hear_myself, bridge=self.bridge)
        self.out.listeners.append(self._playing)
        return self.out

    def _route_mic(self, on: bool, wait: bool = False) -> None:
        """Modo Soundpad: mientras Bubble está conectado, el micrófono de Windows (el que abre Roblox) es el
        virtual, y Bubble le pasa la voz real del jugador. Si el puente con el micrófono real no arrancó, no se
        cambia: el jugador quedaría mudo.
        """
        if on == self._soundpad:
            return
        self._soundpad = on
        bridge = self.bridge

        def work() -> None:
            from ..voice import devices

            try:
                if on:
                    time.sleep(1.0)  # esperar a que el puente arranque (si falla, se apaga solo)
                    if bridge is None or not bridge.running:
                        self._soundpad = self._soundpad_planned = False
                        log.warning("El puente con tu micrófono no arrancó: Windows queda con tu micrófono")
                        return
                    devices.use_cable_as_default()
                else:
                    devices.restore_real_defaults()
            except Exception:  # noqa: BLE001 - queda el botón «Arreglar Windows»
                log.warning("No se pudo cambiar el micrófono de Windows", exc_info=True)

        if wait:
            work()
        else:
            threading.Thread(target=work, name="bubble-microfono-windows", daemon=True).start()

    def _my_microphone(self):
        from ..voice.devices import real_microphone

        return real_microphone(self.config.mic)

    def _apply(self) -> None:
        """Activa o desactiva cada parte según los interruptores."""
        from ..voice.captions import CaptionBoard
        from ..voice.live import LiveListener
        from ..voice.pipelines import DirectVoice, VoiceSpeaker

        final, quick, speakers = self.models
        self._ensure_out()
        self._open_voice_lane()
        if self.board is None:
            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      is_native=self._is_my_language, on_translated=lambda line: self.app.events.put(("voice_line", line)))
        key = self._cloud_key()
        kind = "nube" if key else "pc"
        if kind != self._kind:
            # Se activó o desactivó Bubble Pro: la escucha y la voz del jugador se rearman según corresponda.
            for part in (self.listener, self.speaker):
                if part:
                    part.stop()
            self.listener = self.speaker = None
            self._kind = kind
        if self.subtitles_var.get():
            if self.listener is None and key:
                from ..cloud.deepgram import DeepgramListener
                from ..voice import audio as audio_io

                self.listener = DeepgramListener(key, self.board.caption, source_factory=audio_io.game_audio,
                                                 on_error=lambda msg: self._set_status(msg),
                                                 on_fatal=self._cloud_failed, diarize=self.app.config.pro.diarize,
                                                 earshot=self.earshot, noise_filter=True, speakers=speakers,
                                                 keyterms=self._game_keyterms, recheck=True)
            elif self.listener is None:
                self.listener = LiveListener(final, self.board.caption, partial_asr=quick, speakers=speakers,
                                             on_error=lambda msg: self._set_status(msg),
                                             native=self.app.config.user.language, earshot=self.earshot,
                                             noise_filter=True)
            self.listener.start()
        elif self.listener:
            self.listener.stop()

        direct = self.config.speak_mode == "directo"
        if self.speaker is not None and isinstance(self.speaker, DirectVoice) != direct:
            self.speaker.stop()  # cambio de modo
            self.speaker = None
        if self.speak_var.get():
            if self.speaker is None and direct:
                cloud_ear = None
                if key:
                    from ..cloud.deepgram import DeepgramListener

                    cloud_ear = DeepgramListener(key, lambda _caption: None, source_factory=self._my_microphone,
                                                 on_error=lambda msg: self._set_status(msg),
                                                 on_fatal=self._cloud_failed,
                                                 language=self.app.config.user.language, diarize=False,
                                                 judge=self.profile.intonation, noise_filter=True,
                                                 keyterms=self._my_keyterms)
                self.speaker = DirectVoice(final, self.out, self._translate_mine, self.app.config.user.language,
                                           partial_asr=quick, on_event=self._spoke,
                                           target=self.app.translator.outgoing_target,
                                           mic_factory=self._my_microphone, profile=self.profile, listener=cloud_ear)
            elif self.speaker is None:
                self.out.warm_up(self.app.translator.outgoing_target())
                binding = win32.parse_binding(self.config.push_to_talk)
                stream = None
                if key:
                    # Pro: la voz del jugador se envía a la nube mientras mantiene el botón; al terminar, ya está
                    # reconocida.
                    from ..cloud.deepgram import DeepgramListener
                    from ..voice.pipelines import StreamedTurn

                    stream = StreamedTurn(DeepgramListener(
                        key, lambda _caption: None, source_factory=self._my_microphone,
                        on_error=lambda msg: self._set_status(msg), on_fatal=self._cloud_failed,
                        language=self.app.config.user.language, diarize=False, judge=self.profile.intonation,
                        noise_filter=True, keyterms=self._my_keyterms, send_all=True), self.app.config.user.language)
                self.speaker = VoiceSpeaker(self.my_asr(), self.out, self._translate_mine, binding.vk,
                                            self.app.config.user.language, on_event=self._spoke,
                                            mic_factory=self._my_microphone, profile=self.profile, stream=stream,
                                            by_sentence=True)
                self.speaker.on_turn = lambda turn: self.profile.note_times(turn.times)
            self.speaker.start()
            if not self.out.output.is_cable:
                self._set_status("Tu voz traducida suena en tus auriculares. Para que la escuchen los demás, "
                                 "instalá el micrófono virtual acá abajo.")
            else:
                self._set_status("")
        elif self.speaker:
            self.speaker.stop()
        if not self.speak_var.get() and not self.subtitles_var.get():
            self._set_status("")

    # ------------------------------------------------------------ interruptores
    def _toggle_subtitles(self) -> None:
        self.config.subtitles = self.subtitles_var.get()
        save_setting("voice", "subtitles", self.config.subtitles)
        self._update_locks()
        self._toggled(self.config.subtitles)

    def _toggle_speak(self) -> None:
        self.config.speak = self.speak_var.get()
        save_setting("voice", "speak", self.config.speak)
        self._update_locks()
        self._toggled(self.config.speak)

    def _toggle_hear(self) -> None:
        self.config.hear_myself = self.hear_var.get()
        save_setting("voice", "hear_myself", self.config.hear_myself)
        if self.out:
            self.out.hear_myself = self.config.hear_myself

    def _toggle_pass(self) -> None:
        self.config.pass_my_voice = self.pass_var.get()
        save_setting("voice", "pass_my_voice", self.config.pass_my_voice)
        if self.bridge:
            self.bridge.enabled = self.config.pass_my_voice

    def _change_mode(self) -> None:
        self.config.speak_mode = self.mode_var.get()
        save_setting("voice", "speak_mode", self.config.speak_mode)
        self._update_locks()
        if self.speak_var.get():
            self._toggled(True)

    def _change_voice(self) -> None:
        self.set_gender(self.gender_var.get())
        self._try_voice()  # se escucha cómo suena (y queda cargada)

    def set_gender(self, gender: str) -> None:
        """Voz femenina o masculina (desde esta página o desde la barra del juego)."""
        self.config.gender = gender
        self.gender_var.set(gender)
        save_setting("voice", "gender", gender)
        if self.voices:
            self.voices.gender = gender
        self.warm_up()

    def _change_speed(self) -> None:
        from .app_view import later

        self.config.speed = round(float(self.speed_var.get()), 2)
        self.speed_text.configure(text=self._speed_label())
        if self.voices:
            self.voices.speed = self.config.speed
        later(self.app, "speed", lambda: save_setting("voice", "speed", self.config.speed))

    def _change_mic(self, _event=None) -> None:
        choice = self.mic_box.get()
        self.config.mic = "" if choice.startswith("El predeterminado") else choice
        save_setting("voice", "mic", self.config.mic)
        if self.bridge:
            self.bridge.stop()
            self.bridge.mic_name = self.config.mic
            threading.Timer(0.3, self.bridge.start).start()

    def _toggled(self, turned_on: bool) -> None:
        if not self.app.ready:
            self._set_status("Se activa apenas Bubble termine de prepararse.")
        elif turned_on:
            self._prepare(self._apply)
        else:
            self._apply_if_ready()

    def _apply_if_ready(self) -> None:
        if self.models is not None:
            self._apply()

    def _change_key(self) -> None:
        from .overlays import HotkeyCaptureDialog

        def done(spec: str | None) -> None:
            if spec:
                self.config.push_to_talk = spec
                save_setting("voice", "push_to_talk", spec)
                self.ptt_label.configure(text=win32.describe_binding(spec))
                if self.speaker is not None and hasattr(self.speaker, "vk"):
                    self.speaker.vk = win32.parse_binding(spec).vk

        HotkeyCaptureDialog(self.app.root, done)

    def _voices_now(self):
        """Las voces activas: las de la nube con Bubble Pro (más naturales, con personalidad) y las de la PC en
        Basic. Las de la nube usan las de la PC en los idiomas que no cubren.
        """
        from ..voice.tts import Voices

        self.voices = self.voices or Voices()
        self.voices.gender, self.voices.speed = self.config.gender, self.config.speed
        key = self._cloud_key()
        pro_config = self.app.config.pro
        if not key or not pro_config.voices:
            return self.voices
        if self._cloud_voices is None or self._cloud_voices.key != key:
            from ..cloud.speak import CloudVoices

            self._cloud_voices = CloudVoices(key, self.voices, self._cloud_failed, pro_config.personality)
        self._cloud_voices.personality = pro_config.personality
        return self._cloud_voices

    def _sample_language(self) -> str:
        language = "en"
        if self.app.ready and self.app.translator is not None:
            language = self.app.translator.outgoing_target().split("-")[0]
        voices = self._voices_now() if self.voices is not None else None
        return language if voices is None or voices.voice_for(language) else "en"

    def warm_up(self) -> None:
        """Deja lista la voz elegida (se llama al abrir la página «Voz» y al cambiar de voz)."""

        def work() -> None:
            try:
                voices = self._voices_now()
                language = self._sample_language()
                if not voices.is_loaded(language):
                    voices.prepare(language)
            except Exception:  # noqa: BLE001 - es solo para que después salga rápido
                pass

        threading.Thread(target=work, name="bubble-voz-precarga", daemon=True).start()

    def _try_voice(self) -> None:
        """Una frase de prueba en los auriculares del jugador (no llega a Roblox). La frase se guarda: la segunda
        vez suena al instante.
        """

        def work() -> None:
            try:
                from ..voice import audio as audio_io

                voices = self._voices_now()
                language = self._sample_language()
                key = (language, self.config.gender, self.config.speed, type(voices).__name__,
                       getattr(voices, "personality", ""))
                speech = self._samples.get(key)
                if speech is None:
                    if not voices.is_downloaded(language):
                        self._set_status("Descargando esta voz (una sola vez, ~60 MB)…")
                    elif not voices.is_loaded(language):
                        self._set_status("Preparando la voz…")
                    speech = voices.synthesize(SAMPLES.get(language, SAMPLES["en"]), language)
                    if speech is not None:
                        self._samples[key] = speech
                    self._set_status("")
                if speech is not None:
                    audio_io.play(audio_io.monitor_output(), speech.audio, speech.sample_rate)
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo probar la voz: {exc}")

        threading.Thread(target=work, name="bubble-prueba-voz", daemon=True).start()

    def _install_cable(self) -> None:
        self.cable_button.configure(state="disabled", text="Descargando…")

        def work() -> None:
            try:
                from ..voice.bridge import install_cable

                message = install_cable()
            except Exception as exc:  # noqa: BLE001
                message = f"No se pudo instalar: {exc}"
            self._set_status(message)
            self.app.events.put(("voice_cable_done", None))

        threading.Thread(target=work, name="bubble-instalar-cable", daemon=True).start()

    def fix_windows(self) -> None:
        """El micrófono y el parlante reales del jugador, otra vez como predeterminados de Windows."""

        def work() -> None:
            try:
                from ..voice.devices import restore_real_defaults

                fixed = restore_real_defaults()
                self._set_status("Hecho: Windows vuelve a usar " + " y ".join(fixed) + "." if fixed
                                 else "Windows ya usaba tus dispositivos de siempre.")
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No lo pude cambiar ({exc}). Hacelo en la configuración de sonido de Windows.")
            self._scan_devices()

        threading.Thread(target=work, name="bubble-arreglar-windows", daemon=True).start()

    def cable_done(self) -> None:
        self.cable_button.configure(state="normal", text="Instalar (gratis)")
        threading.Thread(target=self._scan_devices, daemon=True).start()

    # ------------------------------------------------------------ lo que te dicen: frase → traducción → subtítulo
    def _translate_heard(self, text: str, language: str, speaker: int, on_piece, on_done, intonation: str = "") -> None:
        if self._is_my_language(text, language):
            on_done(None, native=True)  # en el idioma del jugador: no se traduce (se ve el original, o nada)
            return

        async def translate() -> None:
            try:
                result = await self.app.translator.translate_incoming(text, speaker_name(speaker), on_delta=on_piece,
                                                                      from_speech=True, intonation=intonation)
                if result.status == "same_language":
                    on_done(None, native=True)
                else:
                    on_done(result.translation if result.status != "error" and result.translation.strip() else None)
            except Exception:  # noqa: BLE001 - sin traducción queda el original
                on_done(None)

        self.app.runner.submit(translate())

    def _is_my_language(self, text: str, language: str) -> bool:
        """¿Ya está en el idioma del jugador? Ese idioma nunca se traduce. Whisper a veces confunde el español
        rioplatense con portugués o italiano: además de lo que detecta Whisper, se analizan las palabras.
        """
        from ..translate.langdetect import foreign_words, native_by_words, without_gaming

        mine = self.app.config.user.language.split("-")[0].lower()
        detector = getattr(self.app.translator, "detector", None)
        detection = detector.detect(without_gaming(text)) if detector is not None else None
        if language.split("-")[0].lower() == mine:
            return not (detection and detection.lang != mine and detection.is_confident(0.8))
        if detection and detection.lang == mine and detection.is_confident(0.5):
            return True
        return native_by_words(text, mine) and not foreign_words(text, mine)

    # ------------------------------------------------------------ tu voz: texto en tu idioma → (traducción, idioma)
    def _translate_mine(self, text: str, intonation: str = "", on_sentence=None) -> tuple[str, str] | None:
        """Frase del jugador → (traducción, idioma). `on_sentence(oración, idioma)`: se invoca con cada oración
        apenas está traducida (para empezar a pronunciarla de inmediato; ver pipelines.Sentences).
        """
        from ..voice.pipelines import Sentences

        translator = self.app.translator
        target = translator.outgoing_target()
        voice = translator.voice_locale(target)  # (con la región elegida, si hay una)
        marks = set(intonation.split("+")) if intonation else set()
        key = text + ("?" if "question" in marks and "?" not in text else "") + (
            "!" if marks & {"shout", "exclaim"} and "!" not in text else "")
        saved = self.profile.saved(key, target)
        if saved:  # ya se dijo antes: sale al instante
            translator.remember_target(target)
            self.app.tracker.mark_sent(saved)
            if on_sentence:
                on_sentence(saved, voice)
            return saved, voice
        sentences = Sentences(lambda sentence: on_sentence(sentence, voice)) if on_sentence else None
        result = self.app.runner.submit(
            translator.translate_outgoing(text, target, tone=self.app.config.user.tone, spoken=True,
                                          from_speech=True, intonation=intonation,
                                          on_delta=sentences.add if sentences else None)).result(timeout=25)
        if result.status == "error" or not result.translation.strip():
            return None
        if sentences:
            sentences.finish(result.translation)
        translator.remember_target(target)
        self.profile.remember(key, target, result.translation)
        self.app.tracker.mark_sent(result.translation)
        return result.translation, voice

    def my_asr(self):
        """Con qué se reconoce la voz del jugador: con Bubble Pro, la nube (y si falla, la PC); si no, el modelo
        habitual ("small" en esta PC). Los modelos locales grandes (large-v3-turbo) repetían las palabras cortas
        y tardaban 2 s con este método (ver asr.py).
        """
        key = self._cloud_key()
        if key:
            from ..cloud.deepgram import DeepgramClip, WithFallback

            return WithFallback(DeepgramClip(key, keyterms=self._my_keyterms), self.models[0], self._cloud_failed)
        return self.models[0]

    # ------------------------------------------------------------ Bubble Pro
    def _cloud_key(self) -> str:
        """La clave de la nube si Bubble Pro está activo (si no, "")."""
        from .. import pro

        if not pro.active():
            return ""
        from ..cloud.keys import load_key

        return load_key()

    def _chat_names(self) -> list[str]:
        """Los jugadores que aparecen en el chat (los más recientes primero)."""
        tracker = getattr(self.app, "tracker", None)
        return tracker.names.recent(20) if tracker is not None else []

    def _my_keyterms(self) -> list[str]:
        """Palabras que la nube debe reconocer correctamente: las aprendidas del jugador, los nombres de los
        jugadores del chat (tal como se pronuncian) y la jerga de juego.
        """
        from ..cloud.deepgram import GAME_TERMS, spoken_names

        return [*self.profile.vocabulary(self.app.config.user.language)[::-1], *spoken_names(self._chat_names()),
                *GAME_TERMS]

    def _game_keyterms(self) -> list[str]:
        """Palabras que la nube debe reconocer en las voces del juego: los nombres del chat y la jerga de Roblox."""
        from ..cloud.deepgram import GAME_TERMS, spoken_names

        return [*spoken_names(self._chat_names()), *GAME_TERMS]

    def _vocabulary_for_claude(self, language: str) -> tuple[str, ...]:
        """Vocabulario para Claude al traducir la voz del jugador: sus palabras y los nombres de los jugadores del
        chat. Si el reconocimiento entendió mal un nombre, Claude lo corrige por el de sonido más parecido.
        """
        return (*self.profile.vocabulary(language), *self._chat_names())

    def _cloud_failed(self, error) -> None:
        """Falla de la nube. Sin conexión, se usa el reconocimiento local para esa frase. Sin saldo o con clave
        inválida, se desactiva Pro.
        """
        from ..cloud.deepgram import BadKey, NoCredit

        fatal = isinstance(error, (BadKey, NoCredit))
        now = time.monotonic()
        if not fatal and now - self._cloud_warned < 60:
            return
        self._cloud_warned = now
        if fatal:
            self._set_status(f"Bubble Pro: {error}. Sigo con el reconocimiento de tu PC.")
            self.app.events.put(("call", lambda: self.app.set_pro(False, reason=str(error))))
        else:
            self._set_status(f"La nube no respondió ({error}). Mientras tanto sigo con tu PC.")

    def pro_changed(self) -> None:
        """(hilo de la ventana) Se activó o desactivó Bubble Pro, o cambió uno de sus ajustes: se reconfiguran la
        escucha y la voz del jugador.
        """
        self._kind = ""
        if self.out is not None:
            self.out.voices = self._voices_now()
        if self.models is not None and not self._cloud_key():
            for model in self.models[:2]:
                if model is not None and hasattr(model, "load_soon") and not model.loaded:
                    model.load_soon()  # vuelve a Basic: se carga de inmediato el reconocimiento local
        if self.models is not None and (self.subtitles_var.get() or self.speak_var.get()):
            self._apply()

    def _open_voice_lane(self) -> None:
        """Carril rápido de Claude para la voz: se abre una sola vez y se mantiene activo."""
        translator = self.app.translator
        if translator is None:
            return
        translator.examples_for = self.profile.examples
        translator.vocabulary_for = self._vocabulary_for_claude

        async def open_lane() -> None:
            translator.start_voice()

        self.app.runner.submit(open_lane())

    def language_changed(self) -> None:
        """El jugador eligió otro idioma para hablar: se deja lista su voz. Con Tab se recorren varios idiomas
        seguidos, por lo que solo se prepara el último, tras una breve espera. Cargar (y a veces descargar) la
        voz de cada idioma intermedio, dos veces, bloqueaba la PC durante el juego.
        """
        if self._warm_after is not None:
            self.app.root.after_cancel(self._warm_after)
        self._warm_after = self.app.root.after(WARM_DELAY_MS, self._warm_language)

    def _warm_language(self) -> None:
        self._warm_after = None
        if self.out is not None and self.app.translator is not None:
            self.out.warm_up(self.app.translator.outgoing_target())

    def _watch_roblox_mic(self) -> None:
        """Cada pocos segundos comprueba si Roblox está grabando del micrófono de Bubble. Si Roblox se abrió antes
        que Bubble, sigue usando el micrófono habitual y los demás no escuchan la voz traducida; se avisa una
        sola vez, en la ventana y en el juego.
        """
        from ..voice.devices import is_virtual, roblox_microphone

        warned = None
        while True:
            time.sleep(5)
            if not (self._soundpad and self.config.speak):
                continue
            try:
                name = roblox_microphone()
                pid = win32.roblox_pid() if hasattr(win32, "roblox_pid") else None
            except Exception:  # noqa: BLE001 - es solo un aviso
                continue
            if name is None or is_virtual(name):
                if warned is not None and name is not None:
                    warned = None
                    self._set_status("Roblox ya usa el micrófono de Bubble: te escuchan.")
                continue
            if warned == pid:
                continue
            warned = pid
            self._set_status(f"Roblox está usando «{name}», así que no escuchan tu voz traducida. En Roblox presioná "
                             "Esc, ingresá en Configuración y en «Dispositivo de entrada» elegí «CABLE Output». "
                             "También podés cerrar Roblox y abrirlo de nuevo.")
            self.app.events.put(("voice_notice", ("Roblox no usa el micrófono de Bubble. En Configuración, "
                                                  "elegí «CABLE Output» como entrada")))

    def notice(self, text: str) -> None:
        """(hilo de la ventana) Muestra un aviso en el juego, donde van los subtítulos."""
        if self.board is None:
            from ..voice.captions import CaptionBoard

            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      is_native=self._is_my_language, on_translated=lambda line: self.app.events.put(("voice_line", line)))
        self.board.notice(text)

    def _spoke(self, kind: str, text: str) -> None:
        if kind == "escuchando":
            self._set_status("Te escucho: hablá y, cuando termines, lo traduzco y lo digo.")
        elif kind == "entendi":
            self._set_status(f"Entendí: «{text}»")
        elif kind == "traduccion":
            self._set_status("")
            self.app.events.put(("voice_subtitle", ("(vos)", text, "→")))
        elif kind == "error":
            self._set_status(f"Tu voz: {text}")

    def _playing(self, seconds: float) -> None:
        # Si la voz traducida suena en los parlantes del jugador (sin micrófono virtual, o por elección), no debe
        # subtitularse como si fuera de otra persona.
        out = self.out
        if out and self.listener and (not out.output.is_cable or out.hear_myself):
            self.listener.muted_until = time.monotonic() + seconds + 0.5
        if out and out.output.is_cable:
            # Bubble no desmutea el micrófono: si el jugador está muteado en Roblox, nadie oye la voz traducida. Se
            # avisa.
            try:
                from ..roblox_mic import roblox_muted

                if roblox_muted():
                    self._set_status("Tenés el micrófono silenciado en Roblox. Activalo (arriba a la izquierda) para "
                                     "que te escuchen.")
            except Exception:  # noqa: BLE001 - es solo un aviso
                pass

    # ------------------------------------------------------------ escribir y que se diga en voz (Ctrl+Enter)
    def say(self, pairs: list[tuple[str, str]], original: str) -> None:
        """Reproduce las traducciones (idioma, texto) con la voz sintética, en orden. No bloquea."""

        def work() -> None:
            try:
                out = self._ensure_out()
                if not out.output.is_cable:
                    self._set_status("Sonó solo en tus auriculares. Para que llegue a Roblox, instalá el "
                                     "micrófono virtual en la página Voz.")
                for language, text in pairs:
                    self.app.events.put(("voice_subtitle", (original, text, language)))
                    if not out.say(text, language):
                        self._set_status(f"No hay voz sintética para el idioma «{language}».")
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo decir en voz: {exc}")

        threading.Thread(target=work, name="bubble-escrito-a-voz", daemon=True).start()

    # ------------------------------------------------------------ subtítulos en pantalla
    def show(self, original: str, translation: str, language: str) -> None:
        """Voz traducida del jugador: se muestra como una frase propia."""
        if self.board is None:
            from ..voice.captions import CaptionBoard

            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      is_native=self._is_my_language, on_translated=lambda line: self.app.events.put(("voice_line", line)))
        self.board.mine(original, translation, language)

    def _tick(self) -> None:
        visible = self.app._in_game()  # con la barra de escritura abierta, los subtítulos siguen visibles
        if self.speaker is not None and hasattr(self.speaker, "set_listening"):
            # Traducción directa (sin botón): solo con Roblox en primer plano. Evita traducir lo dicho fuera del juego
            # (Discord, navegador, etc.).
            self.speaker.set_listening(visible)
        now = time.monotonic()
        self._out_of_game = 0.0 if visible else (self._out_of_game or now)
        if self.listener is not None:
            # Voces del juego: si el jugador lleva un tiempo fuera del juego (los subtítulos no se ven), no se escuchan:
            # con Pro no se paga y en Basic no se consume procesador. Un Alt+Tab breve no las interrumpe.
            away = bool(self._out_of_game) and now - self._out_of_game > OUT_OF_GAME_S
            muted = self.listener.muted_until == float("inf")
            if away and not muted:
                self.listener.muted_until = float("inf")
            elif not away and muted:
                self.listener.muted_until = 0.0
        area = self.app._game_area() if visible else None
        lines = self.board.visible() if self.board is not None else []
        self.subtitles.update(lines, area, visible)
        # Más frecuente mientras aparece una frase (animación); si no, cada 80 ms es suficiente.
        self.app.root.after(33 if self.subtitles.animating else 80, self._tick)
