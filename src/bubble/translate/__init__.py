"""Motor de traducción."""

from __future__ import annotations

from ..config import Config
from .engine import Translator
from .router import Router


def build_translator(config: Config, cloud_key: str = "", on_fatal=None) -> Translator:
    """Con `cloud_key` (sin Claude): traduce con el agente de Deepgram, pagado con los créditos de Bubble Pro (ver
    cloud/agent.py). `on_fatal(error)`: se invoca cuando Deepgram no tiene saldo o la clave es inválida.
    """
    from .prompt import build_system_prompt

    prompt = build_system_prompt(config.translation.explain_slang)
    if cloud_key:
        from ..cloud.agent import DeepgramAgentProvider

        # Chat y voz usan conexiones independientes, de modo que la voz no espera al chat. Cada conexión se abre solo
        # cuando hace falta y se cierra tras un período de inactividad, porque se paga por minuto abierto.
        chat = DeepgramAgentProvider(cloud_key, prompt, name="deepgram", on_fatal=on_fatal)
        voice = DeepgramAgentProvider(cloud_key, prompt, name="deepgram-voz", on_fatal=on_fatal)
        # La primera respuesta de cada conexión tarda ~4 s, por lo que se amplía el tiempo de espera antes de
        # considerarla caída.
        translator = Translator(config, Router([chat], timeout_s=config.translation.timeout_s, first_token_s=9.0),
                                voice_router=Router([voice], timeout_s=config.translation.timeout_s, first_token_s=9.0))
        translator.hedge = False  # una segunda conexión para lo mismo se pagaría dos veces
        return translator
    from .claude_subscription import ClaudeSubscriptionProvider

    provider = ClaudeSubscriptionProvider(config.claude, prompt)
    router = Router([provider], timeout_s=config.translation.timeout_s)
    # La voz usa su propia sesión, para no esperar al chat, y sin razonamiento previo: con razonamiento la respuesta
    # tardaba de 2 a 4 s y sin él, ~1,5 s. La sesión se abre solo cuando se usa la voz.
    voice = ClaudeSubscriptionProvider(config.claude, prompt, model=config.claude.voice_model, thinking=False,
                                       name="claude-voz")
    voice_router = Router([voice], timeout_s=config.translation.timeout_s, first_token_s=6.0)
    return Translator(config, router, voice_router=voice_router)


__all__ = ["Translator", "Router", "build_translator"]
