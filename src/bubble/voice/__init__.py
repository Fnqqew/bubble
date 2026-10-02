"""Voz. Todo el audio se procesa localmente en el equipo; Claude solo traduce el texto.

- Voz recibida → subtítulos: audio del juego → frases → Whisper (local) → Claude → subtítulos.
- Voz del jugador → resto de los jugadores: el jugador mantiene presionada una tecla y habla → Whisper → Claude → voz
  sintética (Piper, local) → micrófono virtual (VB-Audio Virtual Cable), que Roblox utiliza como micrófono del jugador.
"""

import sys
import types

# faster-whisper importa «av» (PyAV, 69 MB) solo para abrir archivos de audio, y Bubble le pasa el audio ya
# decodificado: se instala sin él (ver install.WHISPER_PACKAGE) y recibe un módulo vacío en su lugar. Así tampoco se
# carga donde todavía está instalado, y install.tidy() puede borrarlo.
sys.modules.setdefault("av", types.ModuleType("av"))
