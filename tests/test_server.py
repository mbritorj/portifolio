"""Interface web: rotas, exportação e transmissão por WebSocket."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from escriba.config import AppConfig  # noqa: E402
from escriba.server import criar_app  # noqa: E402


@pytest.fixture
def cliente(tmp_path):
    config = AppConfig()
    config.asr.engine = "mock"
    config.output_dir = tmp_path
    config.vozes.store = str(tmp_path / "vozes.json")
    app = criar_app(config)
    with TestClient(app) as cliente:
        cliente.app_state = app.state.escriba
        yield cliente


def test_pagina_inicial_serve_a_interface(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "Escriba" in resposta.text
    assert 'lang="pt-BR"' in resposta.text


def test_estado_inicial_esta_parado(cliente):
    dados = cliente.get("/api/estado").json()
    assert dados["gravando"] is False
    assert dados["segments"] == []


def test_exportacao_em_todos_os_formatos(cliente):
    cliente.app_state.session.add_segment(
        track="sistema", speaker="Participantes", start_s=0, end_s=2, text="Bom dia."
    )
    for formato in ("md", "txt", "srt", "json"):
        resposta = cliente.get(f"/api/exportar?formato={formato}")
        assert resposta.status_code == 200
        assert "Bom dia." in resposta.text


def test_formato_invalido_e_recusado(cliente):
    assert cliente.get("/api/exportar?formato=docx").status_code == 400


def test_encerrar_sem_gravacao_e_recusado(cliente):
    resposta = cliente.post("/api/encerrar")
    assert resposta.status_code == 400
    assert "nenhuma gravação" in resposta.json()["detail"]


def test_ata_sem_transcricao_e_recusada(cliente):
    resposta = cliente.post("/api/ata", json={})
    assert resposta.status_code == 400
    assert "vazia" in resposta.json()["detail"]


def test_iniciar_sem_dispositivo_devolve_erro_explicativo(cliente):
    # Nesta máquina não há placa de áudio; a mensagem precisa dizer o que fazer.
    resposta = cliente.post("/api/iniciar", json={"titulo": "Teste"})
    assert resposta.status_code == 400
    assert resposta.json()["detail"]


def test_websocket_recebe_snapshot_ao_conectar(cliente):
    cliente.app_state.session.add_segment(
        track="sistema", speaker="Participantes", start_s=0, end_s=1, text="Olá."
    )
    with cliente.websocket_connect("/ws") as ws:
        snapshot = ws.receive_json()
    assert snapshot["type"] == "snapshot"
    assert snapshot["gravando"] is False
    assert snapshot["segments"][0]["text"] == "Olá."


def test_evento_do_pipeline_chega_ao_websocket(cliente):
    with cliente.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        cliente.app_state.publicar({"type": "segment", "text": "novo trecho"})
        assert ws.receive_json()["text"] == "novo trecho"


def test_publicar_sem_cliente_conectado_nao_quebra(cliente):
    cliente.app_state.publicar({"type": "segment", "text": "ninguém ouvindo"})


# ------------------------------------------------------------------- falantes


def semear_falantes(cliente):
    session = cliente.app_state.session
    session.registrar_falante("S1", "Falante 1")
    session.registrar_falante("S2", "Falante 2")
    session.add_segment(track="sistema", speaker="Falante 1", speaker_id="S1",
                        start_s=0, end_s=4, text="Bom dia.")
    session.add_segment(track="sistema", speaker="Falante 2", speaker_id="S2",
                        start_s=5, end_s=9, text="Bom dia, vamos começar.")
    return session


def test_estado_lista_os_falantes(cliente):
    semear_falantes(cliente)
    falantes = cliente.get("/api/estado").json()["speakers"]
    assert [f["label"] for f in falantes] == ["Falante 1", "Falante 2"]
    assert falantes[0]["has_centroid"] is False


def test_renomear_falante_pela_api(cliente):
    session = semear_falantes(cliente)
    resposta = cliente.post("/api/falantes", json={"speaker_id": "S1", "nome": "Ana Souza"})

    assert resposta.status_code == 200
    assert resposta.json()["speaker_id"] == "S1"
    assert session.segments[0].speaker == "Ana Souza"
    assert "Ana Souza" in cliente.get("/api/exportar?formato=md").text


def test_renomear_falante_inexistente_devolve_404(cliente):
    semear_falantes(cliente)
    resposta = cliente.post("/api/falantes", json={"speaker_id": "S9", "nome": "Ana"})
    assert resposta.status_code == 404


def test_renomear_sem_nome_devolve_400(cliente):
    semear_falantes(cliente)
    assert cliente.post("/api/falantes", json={"speaker_id": "S1", "nome": " "}).status_code == 400


def test_renomear_avisa_os_clientes_conectados(cliente):
    semear_falantes(cliente)
    with cliente.websocket_connect("/ws") as ws:
        ws.receive_json()  # snapshot
        cliente.post("/api/falantes", json={"speaker_id": "S1", "nome": "Ana"})
        evento = ws.receive_json()
    assert evento["type"] == "speakers"
    assert evento["speakers"][0]["label"] == "Ana"


def test_cadastro_de_voz_exige_consentimento(cliente):
    session = semear_falantes(cliente)
    session.centroids = {"S1": [0.1, 0.9, 0.2]}
    resposta = cliente.post(
        "/api/vozes", json={"speaker_id": "S1", "nome": "Ana", "consentimento": False}
    )
    assert resposta.status_code == 403
    assert "sensível" in resposta.json()["detail"]


def test_cadastro_de_voz_sem_centroide_e_recusado(cliente):
    semear_falantes(cliente)
    resposta = cliente.post(
        "/api/vozes", json={"speaker_id": "S1", "nome": "Ana", "consentimento": True}
    )
    assert resposta.status_code == 400
    assert "encerre a gravação" in resposta.json()["detail"]


def test_cadastro_de_voz_persiste_e_aparece_na_listagem(cliente):
    session = semear_falantes(cliente)
    session.centroids = {"S1": [0.1, 0.9, 0.2]}
    cliente.post("/api/falantes", json={"speaker_id": "S1", "nome": "Ana Souza"})

    resposta = cliente.post(
        "/api/vozes", json={"speaker_id": "S1", "nome": "Ana Souza", "consentimento": True}
    )
    assert resposta.status_code == 200
    assert resposta.json()["name"] == "Ana Souza"

    vozes = cliente.get("/api/vozes").json()["vozes"]
    assert [v["name"] for v in vozes] == ["Ana Souza"]


def test_listagem_de_vozes_vazia_quando_nao_ha_cadastro(cliente):
    assert cliente.get("/api/vozes").json() == {"vozes": []}


def test_exportacao_para_o_claude_pela_api(cliente):
    semear_falantes(cliente)
    resposta = cliente.get("/api/exportar?formato=claude")
    assert resposta.status_code == 200
    assert "## Quem falou" in resposta.text
    assert "Bom dia." in resposta.text


def test_pedido_de_ata_esta_disponivel_para_copiar(cliente):
    resposta = cliente.get("/api/pedido-ata")
    assert resposta.status_code == 200
    assert "Tarefa | Responsável | Prazo" in resposta.text
    assert "não invente" in resposta.text.lower()


def test_encerrar_salva_tambem_o_arquivo_do_claude(cliente):
    """Ao encerrar, o arquivo para anexar no Claude já fica pronto em disco."""

    class PipelineFalso:
        rodando = False

        def stop(self):
            pass

    semear_falantes(cliente)
    cliente.app_state.pipeline = PipelineFalso()

    arquivos = cliente.app_state.encerrar()

    assert any(a.endswith(".claude.md") for a in arquivos)
    assert any(a.endswith(".json") for a in arquivos)
