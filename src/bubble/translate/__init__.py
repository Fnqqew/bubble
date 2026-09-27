"""Motor de traducción."""

from __future__ import annotations

from ..config import Config
from .engine import Translator
from .router import Router


def build_translator(config: Config) -> Translator:
    from .claude_subscription import ClaudeSubscriptionProvider
    from .prompt import build_system_prompt

    prompt = build_system_prompt(config.translation.explain_slang)
    provider = ClaudeSubscriptionProvider(config.claude, prompt)
    router = Router([provider], timeout_s=config.translation.timeout_s)
    # La voz, por su carril: su propia sesión (no espera detrás del chat), sin pensar antes de responder (pensando
    # tardaba de 2 a 4 s; sin pensar, ~1,5 s: medido). Se abre recién cuando se usa la voz.
    voice = ClaudeSubscriptionProvider(config.claude, prompt, model=config.claude.voice_model, thinking=False,
                                       name="claude-voz")
    voice_router = Router([voice], timeout_s=config.translation.timeout_s, first_token_s=6.0)
    return Translator(config, router, voice_router=voice_router)


__all__ = ["Translator", "Router", "build_translator"]
