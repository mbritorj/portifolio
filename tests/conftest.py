"""Deixa o pacote importável sem instalar, e oferece geradores de áudio sintético."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

TAXA = 16_000


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(20260905)


@pytest.fixture
def gerar_audio(rng):
    """Devolve helpers para montar sinais de fala e silêncio."""

    def fala(duracao: float, frequencia: float = 200.0, amplitude: float = 0.3) -> np.ndarray:
        t = np.arange(int(TAXA * duracao)) / TAXA
        envelope = np.clip(np.sin(np.pi * t / max(duracao, 1e-6)), 0, 1)
        onda = amplitude * envelope * np.sin(2 * np.pi * frequencia * t)
        return (onda + rng.normal(0, 0.0004, t.size)).astype(np.float32)

    def silencio(duracao: float) -> np.ndarray:
        return rng.normal(0, 0.0004, int(TAXA * duracao)).astype(np.float32)

    return fala, silencio


@pytest.fixture
def escrever_wav(tmp_path):
    def _escrever(sinal: np.ndarray, nome: str = "audio.wav", taxa: int = TAXA) -> Path:
        caminho = tmp_path / nome
        with wave.open(str(caminho), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(taxa)
            handle.writeframes((np.clip(sinal, -1, 1) * 32767).astype("<i2").tobytes())
        return caminho

    return _escrever
