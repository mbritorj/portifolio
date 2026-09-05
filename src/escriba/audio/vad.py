"""Detector de atividade de voz por energia adaptativa.

Não depende de modelo nem de biblioteca nativa: o piso de ruído é estimado
continuamente a partir dos blocos silenciosos, e a fala é o que passa dele por
uma margem. Isso segura bem o ruído estacionário de uma chamada (ventoinha,
ar-condicionado, chiado de linha) e roda em qualquer máquina.

A saída são falas (``Utterance``) delimitadas por silêncio, que é a unidade
natural para mandar ao Whisper: transcrever uma frase inteira dá um resultado
muito melhor do que transcrever pedaços de 300 ms.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from ..config import VadConfig
from .resample import dbfs


@dataclass
class Utterance:
    """Um trecho contínuo de fala."""

    audio: np.ndarray
    start_s: float
    end_s: float
    # True quando a fala foi cortada por ``max_utterance_ms`` e continua no
    # próximo trecho; o pipeline usa isso para não fechar o parágrafo.
    truncated: bool = False

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass
class VadState:
    """Estado exposto para a interface (útil para desenhar o medidor de nível)."""

    speaking: bool = False
    level_dbfs: float = float("-inf")
    noise_floor_dbfs: float = float("-inf")


class EnergyVad:
    """Consome blocos de áudio e devolve falas fechadas.

    Uso:

        vad = EnergyVad(VadConfig(), sample_rate=16000, block_ms=30)
        for utterance in vad.process(bloco):
            ...
    """

    def __init__(self, config: VadConfig, sample_rate: int, block_ms: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.block_size = max(1, int(sample_rate * block_ms / 1000))
        self.block_duration = self.block_size / sample_rate

        preroll_blocks = max(1, int(config.preroll_ms / block_ms))
        self._preroll: deque[np.ndarray] = deque(maxlen=preroll_blocks)
        self._hangover_blocks = max(1, int(config.hangover_ms / block_ms))

        self._calibration_blocks = max(0, int(config.calibration_ms / block_ms))
        self._pending = np.zeros(0, dtype=np.float32)
        self._voiced: list[np.ndarray] = []
        self._voiced_blocks = 0
        self._speaking = False
        self._trigger_count = 0
        self._silence_count = 0
        self._noise_floor = float("-inf")
        self._elapsed_blocks = 0
        self._utterance_start_s = 0.0
        self._last_level = float("-inf")

    # ------------------------------------------------------------------ estado

    @property
    def state(self) -> VadState:
        return VadState(
            speaking=self._speaking,
            level_dbfs=self._last_level,
            noise_floor_dbfs=self._noise_floor,
        )

    @property
    def elapsed_s(self) -> float:
        """Tempo de áudio já consumido, em segundos."""
        return self._elapsed_blocks * self.block_duration

    @property
    def current_audio(self) -> np.ndarray:
        """Áudio da fala em andamento (vazio se ninguém está falando).

        É daqui que sai a hipótese parcial mostrada na tela.
        """
        if not self._speaking or not self._voiced:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._voiced)

    @property
    def current_start_s(self) -> float:
        return self._utterance_start_s

    # ------------------------------------------------------------- processamento

    def process(self, samples: np.ndarray) -> list[Utterance]:
        """Consome um pedaço de áudio de tamanho arbitrário e devolve o que fechou."""
        chunk = np.asarray(samples, dtype=np.float32).reshape(-1)
        if chunk.size:
            self._pending = np.concatenate((self._pending, chunk))

        finished: list[Utterance] = []
        while self._pending.size >= self.block_size:
            block = self._pending[: self.block_size]
            self._pending = self._pending[self.block_size :]
            closed = self._process_block(block)
            if closed is not None:
                finished.append(closed)
        return finished

    def flush(self) -> Utterance | None:
        """Fecha a fala em andamento. Chame ao encerrar a captura."""
        if self._pending.size:
            padding = np.zeros(self.block_size - self._pending.size, dtype=np.float32)
            block = np.concatenate((self._pending, padding))
            self._pending = np.zeros(0, dtype=np.float32)
            self._process_block(block)
        if not self._speaking:
            return None
        return self._close_utterance(truncated=False)

    # ---------------------------------------------------------------- internos

    def _process_block(self, block: np.ndarray) -> Utterance | None:
        level = dbfs(block)
        self._last_level = level
        self._elapsed_blocks += 1

        if self._elapsed_blocks <= self._calibration_blocks:
            # Calibração: só mede o ambiente, não abre fala.
            self._update_noise_floor(level)
            self._preroll.append(block)
            return None

        threshold = self._threshold()
        is_voice = level > threshold

        if not is_voice:
            self._update_noise_floor(level)

        if not self._speaking:
            self._preroll.append(block)
            if is_voice:
                self._trigger_count += 1
                if self._trigger_count >= self.config.trigger_frames:
                    self._open_utterance()
            else:
                self._trigger_count = 0
            return None

        self._voiced.append(block)
        if is_voice:
            self._voiced_blocks += 1
            self._silence_count = 0
        else:
            self._silence_count += 1
            if self._silence_count >= self._hangover_blocks:
                return self._close_utterance(truncated=False)

        voiced_ms = len(self._voiced) * self.block_duration * 1000
        if voiced_ms >= self.config.max_utterance_ms:
            return self._close_utterance(truncated=True)
        return None

    def _threshold(self) -> float:
        floor = self.config.absolute_floor_dbfs
        if self._noise_floor == float("-inf"):
            return floor
        return max(floor, self._noise_floor + self.config.speech_margin_db)

    def _update_noise_floor(self, level: float) -> None:
        if level == float("-inf"):
            return
        if self._noise_floor == float("-inf"):
            self._noise_floor = level
            return
        # Sobe devagar e desce rápido: uma pausa longa reencontra o silêncio real
        # sem que uma tosse eleve o piso e mate as falas seguintes.
        alpha = self.config.noise_adapt if level > self._noise_floor else 0.5
        self._noise_floor = (1 - alpha) * self._noise_floor + alpha * level

    def _open_utterance(self) -> None:
        self._speaking = True
        self._silence_count = 0
        self._trigger_count = 0
        preroll = list(self._preroll)
        self._preroll.clear()
        self._voiced = preroll
        # Os blocos que dispararam o gatilho já contam como voz.
        self._voiced_blocks = self.config.trigger_frames
        self._utterance_start_s = max(
            0.0, self.elapsed_s - len(preroll) * self.block_duration
        )

    def _close_utterance(self, *, truncated: bool) -> Utterance | None:
        audio = np.concatenate(self._voiced) if self._voiced else np.zeros(0, dtype=np.float32)
        start = self._utterance_start_s
        end = self.elapsed_s
        voiced_ms = self._voiced_blocks * self.block_duration * 1000

        self._speaking = False
        self._voiced = []
        self._voiced_blocks = 0
        self._silence_count = 0
        self._trigger_count = 0

        if truncated:
            # Reabre imediatamente: a pessoa não parou de falar, só estourou o
            # limite do bloco. As falas seguem coladas na linha do tempo.
            self._speaking = True
            self._utterance_start_s = end

        # O corte usa a voz efetiva, não o tamanho do buffer: pré-roll e hangover
        # somam mais de um segundo e deixariam qualquer estalo passar.
        if voiced_ms < self.config.min_utterance_ms:
            return None
        return Utterance(audio=audio, start_s=start, end_s=end, truncated=truncated)
