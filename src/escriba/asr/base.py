"""Interface comum dos motores de transcrição."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class TranscriptionResult:
    text: str
    language: str = "pt"
    # Log-probabilidade média por token; quanto mais perto de 0, mais confiante.
    avg_logprob: float = 0.0
    duration_s: float = 0.0
    words: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class Transcriber(ABC):
    """Motor de fala para texto."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, *, partial: bool = False) -> TranscriptionResult:
        """Transcreve áudio mono float32 em 16 kHz.

        ``partial=True`` sinaliza hipótese provisória: o motor pode trocar
        qualidade por latência, porque o texto será refeito no fechamento da fala.
        """

    def warmup(self) -> None:  # noqa: B027 - gancho opcional
        """Carrega pesos e roda uma inferência curta, para o primeiro trecho real
        da reunião não pagar o custo de inicialização."""

    def close(self) -> None:  # noqa: B027 - gancho opcional
        """Libera o modelo."""
