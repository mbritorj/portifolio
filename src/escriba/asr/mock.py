"""Motor falso: roda o pipeline inteiro sem baixar modelo nem usar GPU.

Usado nos testes e no comando ``escriba autoteste``, que verifica captura, VAD,
servidor e exportação de forma independente da qualidade do reconhecimento.
"""

from __future__ import annotations

import numpy as np

from ..config import AsrConfig
from .base import Transcriber, TranscriptionResult


class MockTranscriber(Transcriber):
    def __init__(self, config: AsrConfig | None = None, texto: str | None = None) -> None:
        self.config = config or AsrConfig()
        self.texto = texto
        self.chamadas = 0

    def transcribe(self, audio: np.ndarray, *, partial: bool = False) -> TranscriptionResult:
        self.chamadas += 1
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        duracao = samples.size / 16_000
        if self.texto is not None:
            texto = self.texto
        else:
            texto = f"[fala simulada de {duracao:.1f}s]"
            if partial:
                texto = f"[parcial {duracao:.1f}s]"
        return TranscriptionResult(
            text=texto,
            language=self.config.language,
            avg_logprob=-0.2,
            duration_s=duracao,
        )
