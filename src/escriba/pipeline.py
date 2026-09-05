"""Orquestração: captura -> VAD -> transcrição -> eventos.

Uma thread por trilha de áudio (microfone e sistema) segmenta a fala em tempo
real; uma única thread de inferência consome a fila, porque o modelo Whisper é
um recurso caro e não vale a pena instanciá-lo duas vezes. Trechos fechados têm
prioridade sobre hipóteses parciais, e parciais desatualizadas são descartadas
em vez de enfileiradas — é isso que segura a latência quando duas pessoas falam
ao mesmo tempo.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .asr import Transcriber, create_transcriber
from .audio.capture import AudioSource
from .audio.vad import EnergyVad
from .config import AppConfig
from .session import Session

logger = logging.getLogger(__name__)

Event = dict[str, Any]
EventHandler = Callable[[Event], None]

PRIORIDADE_FINAL = 0
PRIORIDADE_PARCIAL = 1


@dataclass(order=True)
class _Job:
    prioridade: int
    seq: int
    track: str = field(compare=False)
    label: str = field(compare=False)
    audio: np.ndarray = field(compare=False, default=None)
    start_s: float = field(compare=False, default=0.0)
    end_s: float = field(compare=False, default=0.0)


@dataclass
class Track:
    name: str
    label: str
    source: AudioSource


@dataclass
class PipelineStats:
    blocos: int = 0
    trechos: int = 0
    parciais: int = 0
    parciais_descartadas: int = 0
    overflows: int = 0
    tempo_inferencia_s: float = 0.0
    audio_transcrito_s: float = 0.0

    @property
    def fator_tempo_real(self) -> float:
        """Segundos de processamento por segundo de áudio. Acima de 1 não acompanha."""
        if self.audio_transcrito_s <= 0:
            return 0.0
        return self.tempo_inferencia_s / self.audio_transcrito_s


class TranscriptionPipeline:
    def __init__(
        self,
        config: AppConfig,
        *,
        session: Session | None = None,
        transcriber: Transcriber | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.config = config
        self.session = session or Session()
        self.transcriber = transcriber or create_transcriber(config.asr)
        self.stats = PipelineStats()

        self._tracks: list[Track] = []
        self._handlers: list[EventHandler] = [on_event] if on_event else []
        self._fila: queue.PriorityQueue[_Job | None] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._threads: list[threading.Thread] = []
        self._parada = threading.Event()
        self._rodando = False
        # Última parcial enfileirada por trilha: quem ficou para trás é descartada.
        self._parcial_atual: dict[str, int] = {}
        self._lock = threading.Lock()
        self._t0 = 0.0

    # -------------------------------------------------------------- configuração

    def add_track(self, name: str, label: str, source: AudioSource) -> None:
        if self._rodando:
            raise RuntimeError("adicione as trilhas antes de iniciar o pipeline.")
        self._tracks.append(Track(name=name, label=label, source=source))

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    @property
    def rodando(self) -> bool:
        return self._rodando

    @property
    def capturando(self) -> bool:
        """True enquanto alguma trilha ainda estiver produzindo áudio."""
        return any(t.is_alive() for t in self._threads if t.name != "asr")

    # ------------------------------------------------------------------- controle

    def start(self) -> None:
        if self._rodando:
            return
        if not self._tracks:
            raise RuntimeError("nenhuma trilha de áudio configurada.")

        self._parada.clear()
        self._rodando = True
        self._t0 = time.monotonic()
        self._emit({"type": "status", "state": "loading_model"})
        self.transcriber.warmup()

        worker = threading.Thread(target=self._loop_inferencia, name="asr", daemon=True)
        worker.start()
        self._threads.append(worker)

        for track in self._tracks:
            thread = threading.Thread(
                target=self._loop_captura, args=(track,), name=f"captura-{track.name}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

        self._emit(
            {
                "type": "status",
                "state": "recording",
                "tracks": [{"track": t.name, "speaker": t.label} for t in self._tracks],
            }
        )

    def stop(self, *, timeout: float = 20.0) -> None:
        if not self._rodando:
            return
        self._parada.set()
        for track in self._tracks:
            track.source.stop()

        limite = time.monotonic() + timeout
        for thread in self._threads:
            if thread.name != "asr":
                thread.join(timeout=max(0.1, limite - time.monotonic()))

        # Sentinela depois das threads de captura: o que já entrou na fila é
        # transcrito antes de encerrar, para não perder a última frase.
        self._fila.put(None)
        for thread in self._threads:
            if thread.name == "asr":
                thread.join(timeout=max(0.1, limite - time.monotonic()))

        self._threads.clear()
        self._rodando = False
        self.session.encerrar()
        self.transcriber.close()
        self._emit({"type": "status", "state": "stopped", "stats": vars(self.stats)})

    def run_until_complete(self, *, parada: threading.Event | None = None) -> None:
        """Roda até as fontes acabarem (caso do WAV) ou até ``parada`` ser sinalizada.

        ``parada`` existe para o Ctrl+C do terminal: o tratador de sinal só marca
        o evento, e o encerramento acontece aqui, fora do contexto do sinal.
        """
        self.start()
        while self.capturando:
            if parada is not None and parada.wait(0.2):
                break
            elif parada is None:
                time.sleep(0.2)
        self.stop()

    # -------------------------------------------------------------------- captura

    def _loop_captura(self, track: Track) -> None:
        vad = EnergyVad(self.config.vad, self.config.audio.sample_rate, self.config.audio.block_ms)
        offset = None
        ultima_parcial = 0.0
        intervalo = self.config.asr.partial_interval_s
        minimo = self.config.asr.partial_min_audio_s

        try:
            for bloco in track.source.blocks():
                if self._parada.is_set():
                    break
                if offset is None:
                    # Diferença entre o início do pipeline e o primeiro bloco desta
                    # trilha; sem isso as duas trilhas ficam em relógios distintos.
                    offset = time.monotonic() - self._t0
                self.stats.blocos += 1

                for utterance in vad.process(bloco):
                    self._enfileirar_final(track, utterance, offset)

                agora = time.monotonic()
                if agora - ultima_parcial >= intervalo:
                    ultima_parcial = agora
                    audio = vad.current_audio
                    if audio.size / self.config.audio.sample_rate >= minimo:
                        self._enfileirar_parcial(track, audio, vad.current_start_s + offset)

            restante = vad.flush()
            if restante is not None:
                self._enfileirar_final(track, restante, offset or 0.0)
        except Exception as exc:  # a captura não pode derrubar o processo todo
            logger.exception("falha na captura da trilha %s", track.name)
            self._emit({"type": "error", "track": track.name, "message": str(exc)})
        finally:
            overflows = getattr(track.source, "overflows", 0)
            self.stats.overflows += overflows
            self._emit({"type": "status", "state": "track_finished", "track": track.name})

    def _enfileirar_final(self, track: Track, utterance, offset: float) -> None:
        self._fila.put(
            _Job(
                prioridade=PRIORIDADE_FINAL,
                seq=next(self._seq),
                track=track.name,
                label=track.label,
                audio=utterance.audio,
                start_s=utterance.start_s + offset,
                end_s=utterance.end_s + offset,
            )
        )

    def _enfileirar_parcial(self, track: Track, audio: np.ndarray, start_s: float) -> None:
        seq = next(self._seq)
        with self._lock:
            self._parcial_atual[track.name] = seq
        self._fila.put(
            _Job(
                prioridade=PRIORIDADE_PARCIAL,
                seq=seq,
                track=track.name,
                label=track.label,
                audio=audio,
                start_s=start_s,
                end_s=start_s + audio.size / self.config.audio.sample_rate,
            )
        )

    # ------------------------------------------------------------------ inferência

    def _loop_inferencia(self) -> None:
        while True:
            job = self._fila.get()
            if job is None:
                break
            try:
                if job.prioridade == PRIORIDADE_PARCIAL and self._parcial_obsoleta(job):
                    self.stats.parciais_descartadas += 1
                    continue
                self._transcrever(job)
            except Exception as exc:
                logger.exception("falha ao transcrever trecho da trilha %s", job.track)
                self._emit({"type": "error", "track": job.track, "message": str(exc)})
            finally:
                self._fila.task_done()

    def _parcial_obsoleta(self, job: _Job) -> bool:
        """Descarta parciais superadas: enquanto uma era transcrita, chegou outra."""
        with self._lock:
            atual = self._parcial_atual.get(job.track)
        return atual is not None and atual != job.seq

    def _transcrever(self, job: _Job) -> None:
        parcial = job.prioridade == PRIORIDADE_PARCIAL
        inicio = time.monotonic()
        resultado = self.transcriber.transcribe(job.audio, partial=parcial)
        decorrido = time.monotonic() - inicio

        self.stats.tempo_inferencia_s += decorrido
        self.stats.audio_transcrito_s += job.audio.size / self.config.audio.sample_rate

        if parcial:
            self.stats.parciais += 1
            self.session.set_partial(
                track=job.track, speaker=job.label, start_s=job.start_s, text=resultado.text
            )
            self._emit(
                {
                    "type": "partial",
                    "track": job.track,
                    "speaker": job.label,
                    "start_s": job.start_s,
                    "text": resultado.text,
                }
            )
            return

        segment = self.session.add_segment(
            track=job.track,
            speaker=job.label,
            start_s=job.start_s,
            end_s=job.end_s,
            text=resultado.text,
            avg_logprob=resultado.avg_logprob,
            low_confidence_limite=self.config.asr.low_confidence_logprob,
        )
        if segment is None:
            self.session.clear_partial(job.track)
            self._emit({"type": "partial", "track": job.track, "speaker": job.label,
                        "start_s": job.start_s, "text": ""})
            return

        self.stats.trechos += 1
        self._emit({"type": "segment", **segment.to_dict(), "latency_s": decorrido})

    # ---------------------------------------------------------------------- eventos

    def _emit(self, evento: Event) -> None:
        for handler in list(self._handlers):
            try:
                handler(evento)
            except Exception:
                logger.exception("assinante de eventos falhou")
