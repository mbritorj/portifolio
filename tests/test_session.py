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


# ------------------------------------------------------------------- falantes


def montar_com_falantes() -> Session:
    session = Session("Reunião com diarização")
    session.registrar_falante("S1", "Falante 1")
    session.registrar_falante("S2", "Falante 2")
    session.add_segment(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=0, end_s=4, text="Bom dia a todos.")
    session.add_segment(track="sistema", speaker="Falante 2", speaker_id="S2",
                        start_s=5, end_s=9, text="Bom dia, podemos começar.")
    session.add_segment(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=30, end_s=33, text="Fechado então.")
    return session


def test_resumo_de_falantes_conta_tempo_e_trechos():
    falantes = {f["speaker_id"]: f for f in montar_com_falantes().speakers}
    assert falantes["S1"]["segments"] == 2
    assert falantes["S1"]["total_s"] == 7.0
    assert falantes["S2"]["segments"] == 1


def test_renomear_falante_atinge_toda_a_transcricao():
    session = montar_com_falantes()
    resultado = session.renomear_falante("S1", "Ana Souza")

    assert resultado == {"speaker_id": "S1", "absorbed": []}
    assert [s.speaker for s in session.segments if s.speaker_id == "S1"] == ["Ana Souza"] * 2
    assert "Ana Souza" in session.to_markdown()


def test_renomear_para_nome_existente_funde_as_vozes():
    session = montar_com_falantes()
    session.renomear_falante("S1", "Ana")
    resultado = session.renomear_falante("S2", "Ana")

    assert resultado["absorbed"] == ["S2"]
    assert {s.speaker_id for s in session.segments} == {"S1"}
    assert len(session.speakers) == 1
    # Com uma voz só, os três trechos viram um parágrafo por bloco de tempo.
    assert session.speakers[0]["segments"] == 3


def test_renomear_falante_desconhecido_falha():
    with pytest.raises(KeyError):
        montar_com_falantes().renomear_falante("S9", "Ana")


def test_renomear_com_nome_vazio_falha():
    with pytest.raises(ValueError, match="vazio"):
        montar_com_falantes().renomear_falante("S1", "  ")


def test_centroides_sobrevivem_a_ida_e_volta_pelo_json():
    session = montar_com_falantes()
    session.centroids = {"S1": [0.1, 0.2], "S2": [0.3, 0.4]}

    copia = Session.from_json(session.to_json())
    assert copia.centroids["S1"] == [0.1, 0.2]
    assert [s.speaker_id for s in copia.segments] == ["S1", "S2", "S1"]
    assert {f["label"] for f in copia.speakers} == {"Falante 1", "Falante 2"}


def test_parcial_carrega_o_falante():
    session = Session()
    session.set_partial(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=1.0, text="bom di")
    assert session.partials[0]["speaker_id"] == "S1"


# ----------------------------------------------------- exportação para o Claude


def test_exportacao_para_claude_lista_quem_falou():
    session = montar_com_falantes()
    session.renomear_falante("S1", "Ana Souza")
    session.add_segment(track="microfone", speaker="Eu", start_s=40, end_s=48,
                        text="Eu fecho com a distribuidora até sexta.")

    texto = session.to_claude()

    assert "## Quem falou" in texto
    assert "**Ana Souza**" in texto and "nome informado por quem gravou" in texto
    # Quem gravou também é participante: costuma ser dono de tarefa na ata.
    assert "**Eu**" in texto and "microfone de quem gravou" in texto
    assert "## Transcrição" in texto
    assert "Eu fecho com a distribuidora" in texto


def test_exportacao_diz_quando_o_nome_veio_do_cadastro_de_voz():
    session = Session("Reunião")
    session.registrar_falante("S1", "Ana Souza", source="perfil", profile_id="ana-souza")
    session.add_segment(track="sistema", speaker="Ana Souza", speaker_id="S1",
                        start_s=0, end_s=6, text="Bom dia.")
    assert "voz reconhecida pelo cadastro" in session.to_claude()


def test_exportacao_avisa_sobre_vozes_sem_nome():
    texto = montar_com_falantes().to_claude()
    assert "Falante 1, Falante 2" in texto
    assert "Não deduza quem são" in texto


def test_exportacao_sem_anonimos_nao_traz_o_aviso():
    session = Session("Reunião")
    session.registrar_falante("S1", "Ana Souza", source="manual")
    session.add_segment(track="sistema", speaker="Ana Souza", speaker_id="S1",
                        start_s=0, end_s=5, text="Bom dia.")
    assert "Não deduza quem são" not in session.to_claude()


def test_exportacao_marca_trechos_de_baixa_confianca():
    session = Session("Reunião")
    session.add_segment(track="sistema", speaker="Participantes", start_s=0, end_s=4,
                        text="prazo de doze semanas", avg_logprob=-1.8)
    texto = session.to_claude()
    assert "prazo de doze semanas (?)" in texto
    assert "baixa confiança" in texto


def test_exportacao_declara_a_origem_automatica():
    """O modelo precisa saber que o texto tem erro de reconhecimento."""
    texto = montar_com_falantes().to_claude()
    assert "reconhecimento automático" in texto
    assert "Falas simultâneas não são separadas" in texto


def test_salvar_gera_o_arquivo_do_claude_com_nome_proprio(tmp_path):
    caminhos = montar_com_falantes().salvar(tmp_path, formatos=("md", "claude"))
    nomes = [c.name for c in caminhos]
    assert any(n.endswith(".claude.md") for n in nomes)
    assert any(n.endswith(".md") and not n.endswith(".claude.md") for n in nomes)
