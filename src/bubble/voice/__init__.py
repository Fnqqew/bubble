"""Voz. Todo el audio se procesa localmente en el equipo; Claude solo traduce el texto.

- Voz recibida → subtítulos: audio del juego → frases → Whisper (local) → Claude → subtítulos.
- Voz del jugador → resto de los jugadores: el jugador mantiene presionada una tecla y habla → Whisper → Claude → voz
  sintética (Piper, local) → micrófono virtual (VB-Audio Virtual Cable), que Roblox utiliza como micrófono del jugador.
"""
