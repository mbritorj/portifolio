"""Motor local baseado em faster-whisper (Whisper compilado em CTranslate2).

Local é a escolha padrão de propósito: o áudio de uma reunião corporativa não
sai da máquina, o que resolve boa parte da conversa sobre LGPD e sigilo antes
de ela começar.
"""

from __future__ import annotations

import logging

import numpy as np

from ..config import AsrConfig
from .base import Transcriber, TranscriptionResult

logger = logging.getLogger(__name__)


class FasterWhisperTranscriber(Transcriber):
    def __init__(self, config: AsrConfig) -> None:
        self.config = config
        self._model = None

    # --------------------------------------------------------------- ciclo de vida

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper não instalado. Use: pip install 'escriba[local]' "
                "(ou pip install faster-whisper)"
            ) from exc

        device, compute_type = self._resolve_runtime()
        logger.info(
            "carregando %s em %s (compute_type=%s)", self.config.model, device, compute_type
        )
        self._model = WhisperModel(
            self.config.model, device=device, compute_type=compute_type
        )
        return self._model

    def _resolve_runtime(self) -> tuple[str, str]:
        device = self.config.device
        if device == "auto":
            device = "cuda" if _tem_cuda() else "cpu"
        compute_type = self.config.compute_type
        if compute_type == "auto":
            if device == "cuda":
                compute_type = "float16"
            else:
                # int8 é o que torna o modelo utilizável em CPU. Em Apple Silicon
                # o ganho da quantização varia com o modelo e a geração do chip:
                # se ficar lento, meça também com compute_type = "float32".
                compute_type = "int8"
        return device, compute_type

    def warmup(self) -> None:
        model = self._ensure_model()
        silencio = np.zeros(16_000, dtype=np.float32)
        segments, _ = model.transcribe(silencio, language=self.config.language, beam_size=1)
        list(segments)

    def close(self) -> None:
        self._model = None

    # ------------------------------------------------------------------ inferência

    def transcribe(self, audio: np.ndarray, *, partial: bool = False) -> TranscriptionResult:
        model = self._ensure_model()
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return TranscriptionResult(text="", language=self.config.language)

        segments, info = model.transcribe(
            samples,
            language=self.config.language,
            beam_size=self.config.partial_beam_size if partial else self.config.beam_size,
            initial_prompt=self.config.initial_prompt or None,
            # O VAD do pipeline já entregou uma fala fechada; o filtro interno
            # aqui só cortaria início e fim de novo.
            vad_filter=False,
            # Sem realimentar o texto anterior: em reunião com várias vozes isso
            # é a origem clássica dos loops de repetição do Whisper.
            condition_on_previous_text=False,
            word_timestamps=not partial,
        )

        partes: list[str] = []
        logprobs: list[float] = []
        palavras: list[dict] = []
        for segment in segments:
            partes.append(segment.text)
            logprobs.append(segment.avg_logprob)
            for word in getattr(segment, "words", None) or []:
                palavras.append(
                    {"word": word.word, "start": word.start, "end": word.end,
                     "probability": word.probability}
                )

        return TranscriptionResult(
            text=" ".join(p.strip() for p in partes).strip(),
            language=getattr(info, "language", self.config.language),
            avg_logprob=float(np.mean(logprobs)) if logprobs else 0.0,
            duration_s=samples.size / 16_000,
            words=palavras,
        )


def _tem_cuda() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # pragma: no cover - depende do ambiente
        return False
