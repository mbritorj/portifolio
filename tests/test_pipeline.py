"""Pipeline de ponta a ponta, sem hardware de áudio e sem modelo de fala."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from escriba.asr.base import TranscriptionResult
from escriba.asr.mock import MockTranscriber
from escriba.audio.capture import WavFileSource
from escriba.config import AppConfig
from escriba.pipeline import TranscriptionPipeline


def montar_config() -> AppConfig:
    config = AppConfig()
    config.asr.engine = "mock"
    return config


def test_wav_vira_transcricao_com_um_trecho_por_fala(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(
        np.concatenate(
            [silencio(0.8), fala(2.0, 190), silencio(1.4), fala(2.0, 240), silencio(0.8)]
        )
    )
    config = montar_config()
    eventos: list[dict] = []
    pipeline = TranscriptionPipeline(
        config, transcriber=MockTranscriber(config.asr), on_event=eventos.append
    )
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho))
    pipeline.run_until_complete()

    segmentos = pipeline.session.segments
    assert len(segmentos) == 2
    assert all(s.speaker == "Participantes" for s in segmentos)
    assert segmentos[0].end_s < segmentos[1].start_s
    assert [e["type"] for e in eventos].count("segment") == 2
    assert eventos[0]["state"] == "loading_model"
    assert eventos[-1]["state"] == "stopped"


def test_duas_trilhas_recebem_rotulos_distintos(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    sinal = np.concatenate([silencio(0.8), fala(1.5), silencio(1.2)])
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    pipeline.add_track("sistema", "Participantes", WavFileSource(escrever_wav(sinal, "a.wav")))
    pipeline.add_track("microfone", "Eu", WavFileSource(escrever_wav(sinal, "b.wav")))
    pipeline.run_until_complete()

    falantes = {s.speaker for s in pipeline.session.segments}
    assert falantes == {"Participantes", "Eu"}


def test_audio_so_de_silencio_nao_gera_trecho(gerar_audio, escrever_wav):
    _, silencio = gerar_audio
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(escrever_wav(silencio(4.0))))
    pipeline.run_until_complete()
    assert pipeline.session.segments == []


def test_transcricao_vazia_nao_vira_segmento(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.8), fala(1.5), silencio(1.2)]))
    config = montar_config()
    pipeline = TranscriptionPipeline(
        config, transcriber=MockTranscriber(config.asr, texto="   ")
    )
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho))
    pipeline.run_until_complete()
    assert pipeline.session.segments == []


def test_falha_do_motor_vira_evento_e_nao_derruba_o_pipeline(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.8), fala(1.5), silencio(1.2)]))

    class MotorQuebrado(MockTranscriber):
        def transcribe(self, audio, *, partial=False):
            raise RuntimeError("modelo indisponível")

    config = montar_config()
    eventos: list[dict] = []
    pipeline = TranscriptionPipeline(
        config, transcriber=MotorQuebrado(config.asr), on_event=eventos.append
    )
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho))
    pipeline.run_until_complete()

    erros = [e for e in eventos if e["type"] == "error"]
    assert erros and "modelo indisponível" in erros[0]["message"]
    assert not pipeline.rodando


def test_parcial_desatualizada_e_descartada(gerar_audio, escrever_wav):
    """Com o motor lento, só a hipótese parcial mais recente é transcrita."""
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.8), fala(6.0), silencio(1.2)]), taxa=16_000)

    class MotorLento(MockTranscriber):
        def transcribe(self, audio, *, partial=False):
            time.sleep(0.15 if partial else 0.01)
            return super().transcribe(audio, partial=partial)

    config = montar_config()
    config.asr.partial_interval_s = 0.01  # força o acúmulo de parciais na fila
    config.asr.partial_min_audio_s = 0.2
    pipeline = TranscriptionPipeline(config, transcriber=MotorLento(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho, realtime=True))
    pipeline.run_until_complete()

    assert pipeline.stats.parciais_descartadas > 0
    assert pipeline.session.segments  # o trecho final saiu apesar do descarte


def test_stop_encerra_captura_ao_vivo(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.5), fala(20.0), silencio(0.5)]))
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho, realtime=True))

    pipeline.start()
    time.sleep(0.5)
    assert pipeline.rodando
    pipeline.stop()
    assert not pipeline.rodando
    assert pipeline.session.encerrada_em is not None


def test_run_until_complete_respeita_o_evento_de_parada(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.5), fala(20.0), silencio(0.5)]))
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho, realtime=True))

    parada = threading.Event()
    threading.Timer(0.4, parada.set).start()
    inicio = time.monotonic()
    pipeline.run_until_complete(parada=parada)
    assert time.monotonic() - inicio < 10  # não esperou os 20 s de áudio
    assert not pipeline.rodando


def test_trilha_so_pode_ser_adicionada_antes_do_start(gerar_audio, escrever_wav):
    _, silencio = gerar_audio
    caminho = escrever_wav(silencio(1.0))
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho))
    pipeline.start()
    with pytest.raises(RuntimeError, match="antes de iniciar"):
        pipeline.add_track("outra", "Outro", WavFileSource(caminho))
    pipeline.stop()


def test_pipeline_sem_trilha_recusa_iniciar():
    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MockTranscriber(config.asr))
    with pytest.raises(RuntimeError, match="nenhuma trilha"):
        pipeline.start()


def test_confianca_baixa_e_sinalizada(gerar_audio, escrever_wav):
    fala, silencio = gerar_audio
    caminho = escrever_wav(np.concatenate([silencio(0.8), fala(1.5), silencio(1.2)]))

    class MotorInseguro(MockTranscriber):
        def transcribe(self, audio, *, partial=False):
            return TranscriptionResult(text="talvez isso", avg_logprob=-2.0)

    config = montar_config()
    pipeline = TranscriptionPipeline(config, transcriber=MotorInseguro(config.asr))
    pipeline.add_track("arquivo", "Participantes", WavFileSource(caminho))
    pipeline.run_until_complete()
    assert pipeline.session.segments[0].low_confidence is True
