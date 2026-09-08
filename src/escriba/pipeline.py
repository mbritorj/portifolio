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
from .diarize import OnlineSpeakerClusterer, SpeakerEmbedder, VoiceProfileStore, create_embedder
from .session import Session

logger = logging.getLogger(__name__)

Event = dict[str, Any]
EventHandler = Callable[[Event], None]

PRIORIDADE_FINAL = 0
PRIORIDADE_PARCIAL = 1
# A sentinela de parada entra depois de tudo: o que já está na fila ainda é
# transcrito. Ela precisa ser um _Job de verdade, e não None — a fila de
# prioridade compara os itens entre si quando os pesos empatam, e um None ali
# derruba a thread de inferência bem na hora de encerrar.
PRIORIDADE_SENTINELA = 2


@dataclass(order=True)
class _Job:
    prioridade: int
    seq: int
    track: str = field(compare=False, default="")
    label: str = field(compare=False, default="")
    audio: np.ndarray = field(compare=False, default=None)
    start_s: float = field(compare=False, default=0.0)
    end_s: float = field(compare=False, default=0.0)
    parar: bool = field(compare=False, default=False)


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
    falantes: int = 0
    tempo_diarizacao_s: float = 0.0

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
        embedder: SpeakerEmbedder | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.config = config
        self.session = session or Session()
        self.transcriber = transcriber or create_transcriber(config.asr)
        self.stats = PipelineStats()

        self.embedder = embedder
        self.clusterer: OnlineSpeakerClusterer | None = None
        self._diarizacao_ativa = config.diarizacao.enabled or embedder is not None
        if self._diarizacao_ativa:
            self.embedder = embedder or create_embedder(config.diarizacao)
            self.clusterer = OnlineSpeakerClusterer(
                threshold=config.diarizacao.threshold,
                max_speakers=config.diarizacao.max_speakers,
                profiles=self._carregar_vozes(),
                profile_threshold=config.vozes.threshold,
                prefixo=config.diarizacao.prefixo,
            )
        # Último falante visto em cada trilha: falas curtas e hipóteses parciais
        # herdam dele em vez de abrir um falante novo com um vetor instável.
        self._ultimo_falante: dict[str, tuple[str, str]] = {}

        self._tracks: list[Track] = []
        self._handlers: list[EventHandler] = [on_event] if on_event else []
        self._fila: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._threads: list[threading.Thread] = []
        self._parada = threading.Event()
        self._rodando = False
        # Última parcial enfileirada por trilha: quem ficou para trás é descartada.
        self._parcial_atual: dict[str, int] = {}
        self._lock = threading.Lock()
        self._t0 = 0.0

    def _carregar_vozes(self) -> VoiceProfileStore | None:
        """Cadastro de vozes, quando existe. Ausência não é erro: sem ele, os
        falantes só ficam sem nome automático."""
        if not self.config.vozes.enabled:
            return None
        caminho = self.config.caminho_vozes()
        if not caminho.exists():
            return None
        try:
            store = VoiceProfileStore(caminho)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("cadastro de vozes ilegível (%s): %s", caminho, exc)
            return None
        return store if len(store) else None

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
        self._aquecer_diarizacao()

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

    def _aquecer_diarizacao(self) -> None:
        """Carrega o modelo de voz antes da reunião começar.

        Se ele faltar, a gravação continua sem diarização: perder a reunião
        inteira por causa de um arquivo ausente seria o pior desfecho possível.
        """
        if not self._diarizacao_ativa or self.embedder is None:
            return
        try:
            self.embedder.embed(np.zeros(self.config.audio.sample_rate, dtype=np.float32))
        except Exception as exc:
            logger.warning("diarização desativada: %s", exc)
            self._diarizacao_ativa = False
            self.clusterer = None
            self._emit(
                {"type": "error", "track": None, "message": f"diarização desativada: {exc}"}
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
        self._fila.put(_Job(prioridade=PRIORIDADE_SENTINELA, seq=next(self._seq), parar=True))
        for thread in self._threads:
            if thread.name == "asr":
                thread.join(timeout=max(0.1, limite - time.monotonic()))

        self._threads.clear()
        self._rodando = False
        if self.clusterer is not None:
            self.session.centroids = self.clusterer.centroids()
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
        # Silêncio digital absoluto: o dispositivo existe e entrega blocos, mas
        # todos zerados. É o que acontece quando o macOS nega o microfone ao
        # terminal ou quando a saída não está roteada para o dispositivo virtual
        # — e, sem este aviso, a tela fica vazia sem nenhuma explicação.
        blocos_mudos = 0
        avisou_mudo = False
        limite_mudo = max(1, int(10_000 / self.config.audio.block_ms))

        try:
            for bloco in track.source.blocks():
                if self._parada.is_set():
                    break
                if offset is None:
                    # Diferença entre o início do pipeline e o primeiro bloco desta
                    # trilha; sem isso as duas trilhas ficam em relógios distintos.
                    offset = time.monotonic() - self._t0
                self.stats.blocos += 1

                if not avisou_mudo:
                    blocos_mudos = 0 if bloco.any() else blocos_mudos + 1
                    if blocos_mudos >= limite_mudo:
                        avisou_mudo = True
                        self._emit(
                            {
                                "type": "error",
                                "track": track.name,
                                "message": (
                                    f"a trilha {track.name!r} está em silêncio absoluto há "
                                    "10 s. Confira a permissão de microfone do terminal e se "
                                    "o áudio da reunião está sendo roteado para o dispositivo "
                                    "escolhido."
                                ),
                            }
                        )

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
            if job.parar:
                self._fila.task_done()
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
            # A hipótese parcial herda o falante da última fala fechada da
            # trilha: extrair vetor de um trecho que ainda está crescendo custa
            # caro e muda de resposta a cada ciclo.
            speaker_id, label = self._ultimo_falante.get(job.track, (None, job.label))
            self.session.set_partial(
                track=job.track, speaker=label, start_s=job.start_s,
                text=resultado.text, speaker_id=speaker_id,
            )
            self._emit(
                {
                    "type": "partial",
                    "track": job.track,
                    "speaker": label,
                    "speaker_id": speaker_id,
                    "start_s": job.start_s,
                    "text": resultado.text,
                }
            )
            return

        speaker_id, label, novo_falante = self._identificar_falante(job)
        segment = self.session.add_segment(
            track=job.track,
            speaker=label,
            start_s=job.start_s,
            end_s=job.end_s,
            text=resultado.text,
            avg_logprob=resultado.avg_logprob,
            low_confidence_limite=self.config.asr.low_confidence_logprob,
            speaker_id=speaker_id,
        )
        if segment is None:
            self.session.clear_partial(job.track)
            self._emit({"type": "partial", "track": job.track, "speaker": job.label,
                        "start_s": job.start_s, "text": ""})
            return

        self.stats.trechos += 1
        self._emit({"type": "segment", **segment.to_dict(), "latency_s": decorrido})
        if novo_falante:
            self.stats.falantes = len(self.session.speakers)
            self._emit({"type": "speakers", "speakers": self.session.speakers})

    def _identificar_falante(self, job: _Job) -> tuple[str | None, str, bool]:
        """Diz de quem é a fala. Devolve (speaker_id, rótulo, é_falante_novo)."""
        if not self._diarizacao_ativa or self.clusterer is None:
            return None, job.label, False
        if job.track not in self.config.diarizacao.tracks:
            # O microfone não precisa de diarização: é sempre a mesma pessoa.
            return None, job.label, False

        duracao = job.audio.size / self.config.audio.sample_rate
        if duracao < self.config.diarizacao.min_audio_s:
            # "Sim", "uhum", "certo": curto demais para um vetor confiável.
            # Herdar quem acabou de falar erra menos do que inventar um falante.
            anterior = self._ultimo_falante.get(job.track)
            return (*anterior, False) if anterior else (None, job.label, False)

        inicio = time.monotonic()
        try:
            embedding = self.embedder.embed(job.audio, self.config.audio.sample_rate)
            atribuicao = self.clusterer.assign(embedding, duracao)
        except Exception as exc:
            logger.exception("falha ao identificar o falante")
            self._emit({"type": "error", "track": job.track, "message": str(exc)})
            return None, job.label, False
        finally:
            self.stats.tempo_diarizacao_s += time.monotonic() - inicio

        speaker = atribuicao.speaker
        self.session.registrar_falante(
            speaker.id, speaker.label, source=speaker.source, profile_id=speaker.profile_id
        )
        self._ultimo_falante[job.track] = (speaker.id, speaker.label)
        return speaker.id, speaker.label, atribuicao.is_new

    def renomear_falante(self, speaker_id: str, nome: str) -> dict[str, Any]:
        """Renomeia uma voz na sessão e no agrupamento, e avisa os assinantes."""
        resultado = self.session.renomear_falante(speaker_id, nome)
        if self.clusterer is not None and self.clusterer.get(speaker_id) is not None:
            try:
                self.clusterer.rename(speaker_id, nome)
            except (KeyError, ValueError):
                logger.debug("falante %s já não existe no agrupamento", speaker_id)
        for trilha, (identificador, _) in list(self._ultimo_falante.items()):
            if identificador in ([speaker_id] + resultado["absorbed"]):
                self._ultimo_falante[trilha] = (resultado["speaker_id"], nome)
        self._emit({"type": "speakers", "speakers": self.session.speakers})
        return resultado

    # ---------------------------------------------------------------------- eventos

    def _emit(self, evento: Event) -> None:
        for handler in list(self._handlers):
            try:
                handler(evento)
            except Exception:
                logger.exception("assinante de eventos falhou")
