"""Motores de transcrição disponíveis."""

from __future__ import annotations

from ..config import AsrConfig
from .base import Transcriber, TranscriptionResult

__all__ = ["Transcriber", "TranscriptionResult", "create_transcriber", "MOTORES"]

MOTORES = ("faster-whisper", "mock")


def create_transcriber(config: AsrConfig) -> Transcriber:
    """Instancia o motor indicado em ``config.engine``."""
    if config.engine == "faster-whisper":
        from .faster_whisper_engine import FasterWhisperTranscriber

        return FasterWhisperTranscriber(config)
    if config.engine == "mock":
        from .mock import MockTranscriber

        return MockTranscriber(config)
    raise ValueError(
        f"motor desconhecido: {config.engine!r}. Opções: {', '.join(MOTORES)}"
    )
