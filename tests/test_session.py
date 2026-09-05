import json

import pytest

from escriba.session import Session


def montar_sessao() -> Session:
    session = Session("Alinhamento semanal")
    session.add_segment(track="sistema", speaker="Participantes", start_s=0.5, end_s=3.0,
                        text="Bom dia, vamos revisar o cronograma.")
    session.add_segment(track="sistema", speaker="Participantes", start_s=3.4, end_s=6.0,
                        text="A entrega do lote 2 escorregou uma semana.")
    session.add_segment(track="microfone", speaker="Eu", start_s=7.0, end_s=9.0,
                        text="Consigo recuperar até sexta.", avg_logprob=-1.5)
    return session


def test_texto_vazio_nao_vira_segmento():
    session = Session()
    assert session.add_segment(track="a", speaker="X", start_s=0, end_s=1, text="   ") is None
    assert session.segments == []


def test_trechos_seguidos_do_mesmo_falante_viram_um_paragrafo():
    texto = montar_sessao().to_text()
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    assert len(linhas) == 2
    assert "cronograma. A entrega do lote 2" in linhas[0]


def test_pausa_longa_separa_os_paragrafos():
    session = Session()
    session.add_segment(track="a", speaker="X", start_s=0, end_s=1, text="primeiro")
    session.add_segment(track="a", speaker="X", start_s=60, end_s=61, text="segundo")
    assert len(session.to_text().strip().splitlines()) == 2


def test_markdown_marca_baixa_confianca():
    markdown = montar_sessao().to_markdown()
    assert "# Alinhamento semanal" in markdown
    assert "_(baixa confiança)_" in markdown
    assert "**[00:00:07] Eu:**" in markdown


def test_srt_usa_virgula_nos_milissegundos():
    session = Session()
    session.add_segment(track="a", speaker="X", start_s=1.5, end_s=2.25, text="oi")
    srt = session.to_srt()
    assert "00:00:01,500 --> 00:00:02,250" in srt
    assert srt.startswith("1\n")


def test_parcial_e_substituida_pelo_trecho_final():
    session = Session()
    session.set_partial(track="a", speaker="X", start_s=0, text="bom di")
    assert session.partials[0]["text"] == "bom di"
    session.add_segment(track="a", speaker="X", start_s=0, end_s=1, text="bom dia")
    assert session.partials == []


def test_parcial_vazia_limpa_a_anterior():
    session = Session()
    session.set_partial(track="a", speaker="X", start_s=0, text="oi")
    session.set_partial(track="a", speaker="X", start_s=0, text="")
    assert session.partials == []


def test_json_ida_e_volta_preserva_a_transcricao():
    original = montar_sessao()
    copia = Session.from_json(original.to_json())
    assert copia.titulo == original.titulo
    assert [s.text for s in copia.segments] == [s.text for s in original.segments]
    assert copia.to_text() == original.to_text()


def test_snapshot_e_serializavel():
    json.dumps(montar_sessao().snapshot())


def test_salvar_gera_um_arquivo_por_formato(tmp_path):
    caminhos = montar_sessao().salvar(tmp_path, formatos=("md", "txt", "srt", "json"))
    assert len(caminhos) == 4
    assert {c.suffix for c in caminhos} == {".md", ".txt", ".srt", ".json"}
    assert all(c.read_text(encoding="utf-8").strip() for c in caminhos)
    assert "alinhamento-semanal" in caminhos[0].name


def test_formato_desconhecido_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="formato desconhecido"):
        Session().salvar(tmp_path, formatos=("docx",))
