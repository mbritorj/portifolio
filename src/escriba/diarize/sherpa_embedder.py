"""Extrator real, em ONNX, via sherpa-onnx.

Modelos de verificação de locutor (CAM++, ERes2NetV2, WeSpeaker, TitaNet) são
treinados para separar vozes, não idiomas: um modelo treinado em inglês e
mandarim funciona em português. O que muda o resultado é a qualidade do áudio,
não a língua.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .base import SpeakerEmbedder, normalizar

logger = logging.getLogger(__name__)

INSTRUCAO_MODELO = (
    "Baixe um modelo de embedding de falante (.onnx) e aponte 'diarizacao.model' "
    "para ele. Sugestão (~28 MB):\n"
    "  curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)


class SherpaEmbedder(SpeakerEmbedder):
    def __init__(self, model: str | Path, *, num_threads: int = 1, provider: str = "cpu") -> None:
        self.model = Path(model)
        self.num_threads = num_threads
        self.provider = provider
        self._extrator = None

    def _ensure(self):
        if self._extrator is not None:
            return self._extrator
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "diarização pede sherpa-onnx. Use: pip install 'escriba[diarizacao]'"
            ) from exc
        if not self.model.is_file():
            raise RuntimeError(f"modelo de voz não encontrado em {self.model}.\n{INSTRUCAO_MODELO}")

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(self.model), num_threads=self.num_threads, provider=self.provider
        )
        if not config.validate():
            raise RuntimeError(f"modelo de voz inválido: {self.model}")
        self._extrator = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.dim = self._extrator.dim
        logger.info("extrator de voz carregado (%s, dim=%d)", self.model.name, self.dim)
        return self._extrator

    def embed(self, audio: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
        extrator = self._ensure()
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        stream = extrator.create_stream()
        stream.accept_waveform(sample_rate, samples)
        stream.input_finished()
        return normalizar(np.array(extrator.compute(stream), dtype=np.float32))

    def close(self) -> None:
        self._extrator = None
