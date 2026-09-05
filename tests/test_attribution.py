"""Atribuição de nomes a partir do transcript oficial da plataforma."""

from __future__ import annotations

import pytest

from escriba.attribution import (
    TranscriptError,
    aplicar,
    estimar_offset,
    ler_transcript,
    mapear,
)
from escriba.session import Session

VTT_TEAMS = """WEBVTT

00:00:04.000 --> 00:00:09.500
<v Ana Souza>Bom dia. A pauta de hoje é o cronograma.</v>

00:00:11.000 --> 00:00:16.000
<v Carlos Pinto>O prazo dos transceivers me preocupa.</v>

00:00:18.000 --> 00:00:23.000
<v Ana Souza>Combinado, você confirma até sexta.</v>
"""

SRT_WEBEX = """1
00:00:04,000 --> 00:00:09,500
Ana Souza: Bom dia. A pauta de hoje é o cronograma.

2
00:00:11,000 --> 00:00:16,000
Carlos Pinto: O prazo dos transceivers me preocupa.
"""

TXT_MEET = """[00:00:04] Ana Souza: Bom dia. A pauta de hoje é o cronograma da migração.
[00:00:11] Carlos Pinto: O prazo dos transceivers me preocupa bastante nesta fase.
[00:00:18] Ana Souza: Combinado, você confirma até sexta-feira sem falta.
"""


def sessao_diarizada(offset: float = 0.0) -> Session:
    """Duas vozes agrupadas, nos mesmos tempos do transcript (mais o offset)."""
    session = Session("Reunião")
    session.registrar_falante("S1", "Falante 1")
    session.registrar_falante("S2", "Falante 2")
    falas = [
        ("S1", 4.0, 9.5, "bom dia a pauta de hoje é o cronograma"),
        ("S2", 11.0, 16.0, "o prazo dos transceivers me preocupa"),
        ("S1", 18.0, 23.0, "combinado você confirma até sexta"),
    ]
    for speaker_id, inicio, fim, texto in falas:
        session.add_segment(
            track="sistema", speaker=f"Falante {speaker_id[-1]}", speaker_id=speaker_id,
            start_s=inicio + offset, end_s=fim + offset, text=texto,
        )
    return session


# ------------------------------------------------------------------- leitura


def test_le_vtt_do_teams(tmp_path):
    caminho = tmp_path / "teams.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    cues = ler_transcript(caminho)

    assert len(cues) == 3
    assert cues[0].speaker == "Ana Souza"
    assert cues[0].start_s == 4.0 and cues[0].end_s == 9.5
    assert "pauta" in cues[0].text and "<v" not in cues[0].text


def test_le_srt_com_nome_antes_dos_dois_pontos(tmp_path):
    caminho = tmp_path / "webex.srt"
    caminho.write_text(SRT_WEBEX, encoding="utf-8")
    cues = ler_transcript(caminho)

    assert [c.speaker for c in cues] == ["Ana Souza", "Carlos Pinto"]
    assert cues[1].text.startswith("O prazo")


def test_le_texto_simples(tmp_path):
    caminho = tmp_path / "meet.txt"
    caminho.write_text(TXT_MEET, encoding="utf-8")
    cues = ler_transcript(caminho)

    assert [c.speaker for c in cues] == ["Ana Souza", "Carlos Pinto", "Ana Souza"]
    assert cues[0].start_s == 4.0
    # Sem fim declarado, a fala não pode invadir a próxima.
    assert cues[0].end_s <= cues[1].start_s


def test_arquivo_sem_falas_e_recusado(tmp_path):
    caminho = tmp_path / "vazio.txt"
    caminho.write_text("nada aqui\noutra linha\n", encoding="utf-8")
    with pytest.raises(TranscriptError, match="Formatos aceitos"):
        ler_transcript(caminho)


# ---------------------------------------------------------------- alinhamento


def test_offset_zero_quando_os_relogios_batem(tmp_path):
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    assert abs(estimar_offset(sessao_diarizada(), ler_transcript(caminho))) < 0.3


@pytest.mark.parametrize("defasagem", [12.0, 45.5, -8.0])
def test_offset_e_descoberto_sozinho(tmp_path, defasagem):
    """A gravação quase nunca começa junto com a reunião."""
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    estimado = estimar_offset(sessao_diarizada(defasagem), ler_transcript(caminho))
    assert abs(estimado - defasagem) < 0.5


def test_mapeamento_nomeia_as_duas_vozes(tmp_path):
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    session = sessao_diarizada(30.0)

    propostas, offset = mapear(session, ler_transcript(caminho))

    assert abs(offset - 30.0) < 0.5
    nomes = {p.speaker_id: p.nome for p in propostas}
    assert nomes == {"S1": "Ana Souza", "S2": "Carlos Pinto"}
    assert all(p.confianca > 0.9 for p in propostas)


def test_offset_explicito_dispensa_a_estimativa(tmp_path):
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    propostas, offset = mapear(sessao_diarizada(20.0), ler_transcript(caminho), offset_s=20.0)
    assert offset == 20.0
    assert len(propostas) == 2


def test_confianca_baixa_e_descartada(tmp_path):
    """Se a voz agrupada cobre dois nomes, não vale afirmar nada."""
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    session = Session()
    session.registrar_falante("S1", "Falante 1")
    session.add_segment(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=4.0, end_s=16.0, text="tudo junto")

    propostas, _ = mapear(session, ler_transcript(caminho), offset_s=0.0, min_confianca=0.8)
    assert propostas == []

    frouxo, _ = mapear(session, ler_transcript(caminho), offset_s=0.0, min_confianca=0.4)
    assert frouxo and frouxo[0].alternativas.keys() == {"Ana Souza", "Carlos Pinto"}


def test_aplicar_renomeia_a_transcricao(tmp_path):
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    session = sessao_diarizada()

    aplicadas = aplicar(session, mapear(session, ler_transcript(caminho))[0])

    assert len(aplicadas) == 2
    assert [s.speaker for s in session.segments] == ["Ana Souza", "Carlos Pinto", "Ana Souza"]
    assert "Carlos Pinto" in session.to_markdown()


def test_duas_vozes_da_mesma_pessoa_sao_fundidas(tmp_path):
    """O agrupamento às vezes parte uma pessoa em dois; o nome oficial junta."""
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    session = Session()
    session.registrar_falante("S1", "Falante 1")
    session.registrar_falante("S2", "Falante 2")
    session.registrar_falante("S3", "Falante 3")
    session.add_segment(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=4.0, end_s=9.5, text="bom dia")
    session.add_segment(track="sistema", speaker="Falante 2", speaker_id="S2",
                        start_s=11.0, end_s=16.0, text="o prazo")
    session.add_segment(track="sistema", speaker="Falante 3", speaker_id="S3",
                        start_s=18.0, end_s=23.0, text="combinado")

    aplicar(session, mapear(session, ler_transcript(caminho), offset_s=0.0)[0])

    assert {s.speaker for s in session.segments} == {"Ana Souza", "Carlos Pinto"}
    assert len(session.speakers) == 2


def test_sessao_sem_diarizacao_nao_gera_proposta(tmp_path):
    caminho = tmp_path / "t.vtt"
    caminho.write_text(VTT_TEAMS, encoding="utf-8")
    session = Session()
    session.add_segment(track="sistema", speaker="Participantes", start_s=4, end_s=9, text="oi")

    propostas, _ = mapear(session, ler_transcript(caminho))
    assert propostas == []
