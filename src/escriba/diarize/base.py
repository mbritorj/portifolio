"""Interface dos extratores de impressão vocal (embeddings de falante)."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class SpeakerEmbedder(ABC):
    """Converte um trecho de fala em um vetor que representa a voz.

    O vetor não guarda o que foi dito, e sim como a pessoa fala. Duas falas da
    mesma pessoa produzem vetores próximos; de pessoas diferentes, distantes.
    """

    #: Dimensão do vetor produzido.
    dim: int = 0

    @abstractmethod
    def embed(self, audio: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
        """Devolve o vetor float32 normalizado da fala."""

    def close(self) -> None:  # noqa: B027 - gancho opcional
        """Libera o modelo."""


def normalizar(vetor: np.ndarray) -> np.ndarray:
    """Normaliza para norma 1, de modo que o produto interno vire cosseno."""
    array = np.asarray(vetor, dtype=np.float32).reshape(-1)
    norma = float(np.linalg.norm(array))
    if norma < 1e-9:
        return array
    return (array / norma).astype(np.float32)


def cosseno(a: np.ndarray, b: np.ndarray) -> float:
    """Similaridade de cosseno entre dois vetores (-1 a 1)."""
    va, vb = normalizar(a), normalizar(b)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    return float(np.dot(va, vb))
