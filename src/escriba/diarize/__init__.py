"""Diarização: separar as vozes e, quando possível, dar nome a elas."""

from __future__ import annotations

from ..config import DiarizacaoConfig
from .base import SpeakerEmbedder, cosseno, normalizar
from .clustering import Assignment, OnlineSpeakerClusterer, Speaker
from .profiles import ConsentimentoAusente, VoiceProfile, VoiceProfileStore

__all__ = [
    "Assignment",
    "ConsentimentoAusente",
    "OnlineSpeakerClusterer",
    "Speaker",
    "SpeakerEmbedder",
    "VoiceProfile",
    "VoiceProfileStore",
    "cosseno",
    "create_embedder",
    "normalizar",
]


def create_embedder(config: DiarizacaoConfig) -> SpeakerEmbedder:
    """Instancia o extrator indicado em ``config.engine``."""
    if config.engine == "sherpa":
        from .sherpa_embedder import SherpaEmbedder

        return SherpaEmbedder(
            config.model, num_threads=config.num_threads, provider=config.provider
        )
    if config.engine == "mock":
        from .mock import MockEmbedder

        return MockEmbedder()
    raise ValueError(f"extrator de voz desconhecido: {config.engine!r}. Opções: sherpa, mock")
