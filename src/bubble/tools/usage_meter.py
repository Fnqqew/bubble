"""Cuánto de tu suscripción de Claude gasta Bubble.

Pasa por el traductor de verdad (tu suscripción, el mismo camino que en el juego) un lote como el de una partida:
mensajes del chat en varios idiomas, frases del chat de voz y mensajes tuyos. Anota lo que informa Claude Code:
- tokens y costo equivalente en la API (lo que costaría pagando por uso);
- cuánto se movió el límite de 5 horas y el semanal de tu plan (lo que de verdad importa con una suscripción).

Uso:  python -m bubble.tools.usage_meter [--modelo opus|sonnet|haiku]   (≈50 traducciones: un uso chico)
Guarda el resultado en %LOCALAPPDATA%\\Bubble\\uso_<modelo>.json.
"""

from __future__ import annotations

import asyncio
import json
import time

from ..config import load_config
from ..translate import build_translator
from ..voice.models import models_dir

# Lo que llega en una partida típica (mensajes reales de grabaciones y ejemplos de otros idiomas).
CHAT = [
    "i'm studying medicine btw", "mess ege me anytime", "me too!", "actually", "before u go",
    "could u donate pls i wanna buy a priv", "umm a lil bit shy", "have u been on mic up?", "its so toxic on there",
    "i got hate rallied on there", "i gor suspended", "i was in a debate", "I support you", "with an incel",
    "yeah if i get habituated i can speak", "he was pro life",
    'The mirror would answer, "You are the most beautiful of all women."',
    "This answer satisfied the queen because she knew that her mirror always told the truth.",
    "Once upon a time, there was a beautiful young princess named Snow White.",
    "vlw mano, tmj! slk esse pet é muito top kkkk", "mds que lag, vc tá travando tb?", "pdp, bora pra base q eu te carrego",
    "ngl this game is mid, lowkey an L fr", "wer will traden? ich hab ein seltenes pet", "quelqu'un veut échanger ?",
    "bhai kidher hai tu, base pe aa", "anyone wanna trade my dragon", "who wants to play hide and seek",
    "bro stop following me", "gg ez", "lets go to the lobby", "can someone help me with this obby",
]
VOICE = [
    "He's spitting, look, you're obsessed with me now", "Like your rule locks character", "Oh, she's probably a girl",
    "You won't go away because you need attention", "No, no, no, stop it", "Where are you guys going?",
    "Wait for me, I'm coming", "That was so funny", "Can you hear me?", "Who's talking right now?",
]
MINE = [
    "hola, alguien quiere tradear?", "dale, vamos a la base", "no te entiendo, lo podés repetir?",
    "estoy lageado perdón", "buenísimo el juego", "me ayudan con esta parte?", "jaja qué bueno",
    "ya vuelvo, voy a comer", "quién habla?", "gracias por la ayuda!",
]


async def run(model: str | None = None) -> dict:
    config = load_config()
    if model:
        config.claude.model = model
    translator = build_translator(config)
    await translator.start()
    provider = translator.router.providers[0]
    usage = provider.usage
    started = time.perf_counter()
    before = dict(usage.utilization)
    counts = {"chat": 0, "voz": 0, "tuyos": 0}
    try:
        for text in CHAT:
            await translator.translate_incoming(text, "Player")
            counts["chat"] += 1
        for text in VOICE:
            await translator.translate_incoming(text, "Voz 1")
            counts["voz"] += 1
        for text in MINE:
            await translator.translate_outgoing(text, "en", spoken=True)
            counts["tuyos"] += 1
    finally:
        await translator.close()
    after = dict(usage.utilization)
    requests = max(1, usage.requests)
    result = {
        "fecha": time.strftime("%Y-%m-%d %H:%M"),
        "modelo": config.claude.model,
        "esfuerzo": config.claude.effort,
        "mensajes": counts,
        "pedidos_a_claude": usage.requests,
        "segundos": round(time.perf_counter() - started, 1),
        "tokens": {"entrada": usage.input_tokens, "salida": usage.output_tokens,
                   "cache_leido": usage.cache_read_tokens, "cache_escrito": usage.cache_write_tokens},
        "costo_api_usd": round(usage.cost_usd, 4),
        "costo_api_por_pedido_usd": round(usage.cost_usd / requests, 5),
        "limites_antes": before,
        "limites_despues": after,
    }
    for window in set(before) | set(after):
        moved = after.get(window, 0.0) - before.get(window, 0.0)
        if moved > 0:
            result.setdefault("limite_por_pedido", {})[window] = moved / requests
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Cuánto de tu suscripción de Claude gasta Bubble")
    parser.add_argument("--modelo", default=None, help="opus (el de siempre), sonnet o haiku")
    args = parser.parse_args()
    result = asyncio.run(run(args.modelo))
    path = models_dir().parent / f"uso_{result['modelo']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
