"""Extrator determinístico para testes: sem modelo, sem download, sem GPU.

Mapeia a frequência dominante do trecho para um vetor estável. Áudios com o
mesmo timbre sintético caem no mesmo ponto do espaço; timbres diferentes caem
longe. É o suficiente para exercitar clustering, cadastro e pipeline.
"""

from __future__ import annotations

import numpy as np

from .base import SpeakerEmbedder, normalizar


class MockEmbedder(SpeakerEmbedder):
    dim = 32

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.chamadas = 0

    def embed(self, audio: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
        self.chamadas += 1
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size < 16:
            return normalizar(np.ones(self.dim, dtype=np.float32))

        espectro = np.abs(np.fft.rfft(samples))
        pico_hz = float(np.fft.rfftfreq(samples.size, 1 / sample_rate)[int(np.argmax(espectro))])
        # Uma gaussiana centrada na frequência dominante: vozes próximas geram
        # vetores próximos, e o cosseno cai rápido quando o timbre muda.
        centros = np.linspace(60, 400, self.dim, dtype=np.float32)
        return normalizar(np.exp(-(((centros - pico_hz) / 25.0) ** 2)).astype(np.float32))
