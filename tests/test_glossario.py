"""Glossário da reunião: persistência, limite e precedência sobre a configuração."""

from __future__ import annotations

from escriba.config import AppConfig
from escriba.glossario import LIMITE_CARACTERES, contar_termos, ler, normalizar, salvar


def test_ler_arquivo_inexistente_devolve_vazio(tmp_path):
    assert ler(tmp_path / "nao-existe.txt") == ""


def test_salvar_normaliza_para_uma_linha(tmp_path):
    caminho = tmp_path / "glossario.txt"
    texto, aviso = salvar(caminho, "  Nokia,\n  Taesa,\n\n  Albino  ")
    assert texto == "Nokia, Taesa, Albino"
    assert aviso is None
    assert ler(caminho) == "Nokia, Taesa, Albino"


def test_texto_longo_demais_e_cortado_em_palavra_inteira(tmp_path):
    texto, aviso = salvar(tmp_path / "g.txt", "Nutanix, " * 200)
    assert len(texto) <= LIMITE_CARACTERES
    assert not texto.endswith(",")
    assert aviso is not None


def test_salvar_vazio_apaga_o_arquivo(tmp_path):
    caminho = tmp_path / "g.txt"
    salvar(caminho, "Nokia")
    assert caminho.exists()
    salvar(caminho, "  ")
    assert not caminho.exists()
    assert ler(caminho) == ""


def test_contagem_de_termos():
    assert contar_termos("") == 0
    assert contar_termos("Nokia") == 1
    assert contar_termos("Nokia, Taesa; Albino,") == 3


def test_normalizar_junta_linhas():
    assert normalizar("Nokia,\nTaesa") == "Nokia, Taesa"


def test_config_usa_o_glossario_salvo(tmp_path, monkeypatch):
    """O que foi salvo na página vale também para a linha de comando."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = AppConfig.load()
    salvar(config.caminho_glossario(), "Nokia, Taesa")

    assert AppConfig.load().asr.initial_prompt == "Nokia, Taesa"


def test_initial_prompt_escrito_a_mao_tem_precedencia(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    salvar(AppConfig.load().caminho_glossario(), "Nokia, Taesa")

    arquivo = tmp_path / "escriba.toml"
    arquivo.write_text('[asr]\ninitial_prompt = "escrito à mão"\n', encoding="utf-8")
    assert AppConfig.load(arquivo).asr.initial_prompt == "escrito à mão"
