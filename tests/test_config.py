import pytest

from escriba.config import AppConfig


def test_padroes_sao_pt_br_e_locais():
    config = AppConfig()
    assert config.asr.language == "pt"
    assert config.asr.engine == "faster-whisper"
    assert config.server.host == "127.0.0.1"  # nada exposto na rede por padrão


def test_toml_sobrescreve_apenas_o_declarado(tmp_path):
    arquivo = tmp_path / "escriba.toml"
    arquivo.write_text(
        '[asr]\nmodel = "small"\n\n[vad]\nhangover_ms = 900\n', encoding="utf-8"
    )
    config = AppConfig.load(arquivo)
    assert config.asr.model == "small"
    assert config.vad.hangover_ms == 900
    assert config.asr.language == "pt"  # intocado


def test_toml_com_chave_desconhecida_falha_cedo(tmp_path):
    arquivo = tmp_path / "escriba.toml"
    arquivo.write_text('[asr]\nmodelo = "small"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="desconhecida"):
        AppConfig.load(arquivo)


def test_arquivo_inexistente_falha(tmp_path):
    with pytest.raises(FileNotFoundError):
        AppConfig.load(tmp_path / "nao-existe.toml")


def test_ambiente_tem_precedencia_sobre_o_toml(tmp_path, monkeypatch):
    arquivo = tmp_path / "escriba.toml"
    arquivo.write_text('[asr]\nmodel = "small"\n', encoding="utf-8")
    monkeypatch.setenv("ESCRIBA_MODEL", "medium")
    monkeypatch.setenv("ESCRIBA_PORT", "9001")
    config = AppConfig.load(arquivo)
    assert config.asr.model == "medium"
    assert config.server.port == 9001


def test_output_dir_vira_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(tmp_path / "atas"))
    config = AppConfig.load()
    assert config.output_dir.name == "atas"
