"""Los dispositivos de Windows, puestos como tienen que estar.

- Al instalar el micrófono virtual (VB-Audio Virtual Cable), Windows suele dejarlo como micrófono o parlante
  predeterminado: como parlante no escuchás nada, y como micrófono nadie te escucha si Bubble está cerrado. Bubble lo
  corrige solo.
- Modo Soundpad: mientras Bubble está abierto, el micrófono de Windows es el virtual, y Bubble le pasa tu voz real en
  vivo más la traducida: te escuchan igual que siempre, y la voz traducida cuando suena. Al cerrar Bubble, vuelve tu
  micrófono. Roblox arma su lista de micrófonos UNA vez, al abrirse, y usa el que en ese momento es el de Windows (se
  ve en su registro): por eso el cambio se hace apenas abre Bubble y no se deshace al cerrar Roblox. Si Roblox ya
  estaba abierto, `roblox_microphone` dice cuál está usando, para avisarte.

Windows no tiene una función pública para elegir el dispositivo predeterminado: se usa la misma interfaz COM que usan
el panel de Sonido y programas como EarTrumpet o SoundSwitch (IPolicyConfig), a mano con ctypes.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import POINTER, byref, c_void_p, wintypes

from .process_audio import GUID, HRESULT, _call, _guid, _release

log = logging.getLogger(__name__)
CLSID_PolicyConfigClient = _guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")
IID_IPolicyConfig = _guid("f8679f50-850a-41cf-9c72-430f290290c8")
CLSCTX_ALL = 23
ROLES = (0, 1, 2)  # consola, multimedia y comunicaciones (Discord usa esta)
VIRTUAL = ("cable", "vb-audio", "voicemeeter")


def is_virtual(name: str) -> bool:
    return any(word in name.lower() for word in VIRTUAL)


def set_default(device_id: str) -> None:
    """Pone ese dispositivo como predeterminado de Windows (para los tres usos)."""
    from .audio import com_ready

    com_ready()
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    policy = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_PolicyConfigClient), None, CLSCTX_ALL, byref(IID_IPolicyConfig),
                           byref(policy))
    try:
        for role in ROLES:
            _call(policy, 13, device_id, role, argtypes=(wintypes.LPCWSTR, ctypes.c_int))  # SetDefaultEndpoint
    finally:
        _release(policy)


def _sc():
    from .audio import _sc as soundcard

    return soundcard()


def _cable_output(sc):
    return next((mic for mic in sc.all_microphones() if "cable output" in mic.name.lower()), None)


def _remember(name: str) -> None:
    from ..state import update_state

    update_state(real_microphone=name)


def _remembered() -> str:
    from ..state import load_state

    return load_state().get("real_microphone", "")


def use_cable_as_default() -> bool:
    """Modo Soundpad: el micrófono virtual como micrófono de Windows (se guarda cuál era el tuyo). False si no hay
    micrófono virtual."""
    sc = _sc()
    cable = _cable_output(sc)
    if cable is None:
        return False
    current = sc.default_microphone()
    if not is_virtual(current.name):
        _remember(current.name)
    if current.name != cable.name:
        set_default(cable.id)
        log.info("Modo Soundpad: el micrófono de Windows ahora es %s (tu voz pasa por Bubble)", cable.name)
    _fix_speaker(sc)
    return True


def _fix_speaker(sc) -> str:
    """El parlante nunca tiene que ser el cable (no escucharías nada)."""
    if is_virtual(sc.default_speaker().name):
        real = next((speaker for speaker in sc.all_speakers() if not is_virtual(speaker.name)), None)
        if real is not None:
            set_default(real.id)
            log.info("El parlante de Windows era el micrófono virtual: vuelve a ser %s", real.name)
            return real.name
    return ""


def wrong_defaults() -> list[str]:
    """Qué quedó en el micrófono virtual: "micrófono", "parlante" (vacío si está todo bien)."""
    sc = _sc()
    wrong = []
    if is_virtual(sc.default_microphone().name):
        wrong.append("micrófono")
    if is_virtual(sc.default_speaker().name):
        wrong.append("parlante")
    return wrong


def restore_real_defaults() -> list[str]:
    """Vuelve a poner tu micrófono y tu parlante de verdad como predeterminados, si el cable quedó en su lugar.
    Devuelve los nombres de lo que puso."""
    sc = _sc()
    fixed = []
    if is_virtual(sc.default_microphone().name):
        mics = [mic for mic in sc.all_microphones() if not is_virtual(mic.name)]
        saved = _remembered()
        real = next((mic for mic in mics if mic.name == saved), mics[0] if mics else None)
        if real is not None:
            set_default(real.id)
            fixed.append(real.name)
    speaker = _fix_speaker(sc)
    if speaker:
        fixed.append(speaker)
    if fixed:
        log.info("Dispositivos de Windows restaurados: %s", ", ".join(fixed))
    return fixed


def roblox_microphone() -> str | None:
    """El micrófono del que está grabando Roblox ahora (su sesión de audio activa), o None si no graba o no está
    abierto. Solo mira: no cambia nada."""
    from .. import win32
    from .sessions import sessions

    pids = set(win32.roblox_process_ids())
    if not pids:
        return None
    for mic in _sc().all_microphones():
        try:
            found = sessions(mic.id)
        except OSError:
            continue
        if any(session.pid in pids and session.active for session in found):
            return mic.name
    return None


def real_microphone(name: str = ""):
    """Tu micrófono de verdad: el elegido en Bubble, o el predeterminado de Windows salvo que sea el virtual."""
    sc = _sc()
    mics = sc.all_microphones()
    if name:
        chosen = next((mic for mic in mics if mic.name == name), None)
        if chosen is not None:
            return chosen
    default = sc.default_microphone()
    if not is_virtual(default.name):
        return default
    saved = _remembered()  # con el modo Soundpad, el de Windows es el virtual: el tuyo es el que estaba antes
    return next((mic for mic in mics if mic.name == saved),
                next((mic for mic in mics if not is_virtual(mic.name)), default))
