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
