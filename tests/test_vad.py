import numpy as np
import pytest

from escriba.audio.vad import EnergyVad
from escriba.config import VadConfig

TAXA = 16_000


def montar(**ajustes) -> EnergyVad:
    return EnergyVad(VadConfig(**ajustes), TAXA, 30)


def test_silencio_nao_gera_fala(gerar_audio):
    _, silencio = gerar_audio
    vad = montar()
    assert vad.process(silencio(3.0)) == []
    assert vad.flush() is None


def test_uma_fala_entre_silencios(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar()
    encontradas = []
    for bloco in (silencio(1.0), fala(2.0), silencio(1.5)):
        encontradas += vad.process(bloco)

    assert len(encontradas) == 1
    utterance = encontradas[0]
    # O pré-roll pega o início da palavra, então a fala abre um pouco antes de 1,0 s.
    assert 0.6 <= utterance.start_s <= 1.05
    # O fim inclui o hangover configurado (700 ms).
    assert 3.0 <= utterance.end_s <= 3.9
    assert not utterance.truncated


def test_duas_falas_separadas_por_pausa_longa(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar()
    encontradas = []
    for bloco in (silencio(0.5), fala(1.5, 190), silencio(1.5), fala(1.5, 240), silencio(1.0)):
        encontradas += vad.process(bloco)
    assert len(encontradas) == 2
    assert encontradas[0].end_s < encontradas[1].start_s


def test_pausa_curta_nao_quebra_a_fala(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar()
    encontradas = []
    # 300 ms de pausa: menos que o hangover, é a respiração no meio da frase.
    for bloco in (silencio(0.5), fala(1.2), silencio(0.3), fala(1.2), silencio(1.2)):
        encontradas += vad.process(bloco)
    assert len(encontradas) == 1


def test_estalo_curto_e_descartado(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar(min_utterance_ms=400)
    encontradas = []
    for bloco in (silencio(0.5), fala(0.12, 300), silencio(1.2)):
        encontradas += vad.process(bloco)
    assert encontradas == []


def test_monologo_e_cortado_no_limite(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar(max_utterance_ms=2000)
    encontradas = []
    for bloco in (silencio(0.4), fala(6.0), silencio(1.2)):
        encontradas += vad.process(bloco)

    assert len(encontradas) >= 3
    assert encontradas[0].truncated is True
    # Os cortes ficam colados: nenhum pedaço de áudio se perde entre eles.
    for anterior, seguinte in zip(encontradas, encontradas[1:], strict=False):
        assert seguinte.start_s == pytest.approx(anterior.end_s, abs=0.05)


def test_flush_fecha_fala_em_andamento(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar()
    vad.process(silencio(0.5))
    assert vad.process(fala(1.5)) == []  # ainda falando, nada fechou
    restante = vad.flush()
    assert restante is not None
    assert restante.duration_s > 1.0


def test_piso_de_ruido_sobe_com_o_ambiente(rng):
    vad = montar()
    ruidoso = rng.normal(0, 0.02, int(TAXA * 4.0)).astype(np.float32)
    assert vad.process(ruidoso) == []  # ruído estacionário não vira fala
    assert vad.state.noise_floor_dbfs > -45


def test_estado_reflete_fala_em_andamento(gerar_audio):
    fala, silencio = gerar_audio
    vad = montar()
    vad.process(silencio(0.5))
    assert vad.state.speaking is False
    vad.process(fala(1.0))
    assert vad.state.speaking is True
    assert vad.current_audio.size > 0
