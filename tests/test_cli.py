import json

import numpy as np
import pytest

from escriba.cli import main


def test_autoteste_valida_o_pipeline_inteiro(capsys):
    assert main(["autoteste"]) == 0
    assert "autoteste OK" in capsys.readouterr().out


def test_arquivo_transcreve_wav_e_salva(tmp_path, gerar_audio, escrever_wav, capsys, monkeypatch):
    fala, silencio = gerar_audio
    caminho = escrever_wav(
        np.concatenate(
            [silencio(0.8), fala(2.0, 190), silencio(1.4), fala(2.0, 240), silencio(0.8)]
        ),
        "reuniao.wav",
    )
    saida = tmp_path / "saida"
    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(saida))

    assert main(["--motor", "mock", "arquivo", str(caminho), "--falante", "Cliente"]) == 0

    gerados = sorted(p.name for p in saida.iterdir())
    assert any(nome.endswith(".md") for nome in gerados)
    dados = json.loads(next(saida.glob("*.json")).read_text(encoding="utf-8"))
    assert len(dados["segments"]) == 2
    assert dados["segments"][0]["speaker"] == "Cliente"


def test_arquivo_inexistente_falha_com_mensagem(tmp_path, capsys):
    assert main(["arquivo", str(tmp_path / "nada.wav")]) == 1
    assert "não existe" in capsys.readouterr().err


def test_ata_exige_arquivo(tmp_path, capsys):
    assert main(["ata", str(tmp_path / "nada.json")]) == 1


def test_wav_de_24_bits_e_recusado_com_instrucao(tmp_path):
    import wave

    from escriba.audio.capture import WavFileSource

    caminho = tmp_path / "24bits.wav"
    with wave.open(str(caminho), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(3)
        handle.setframerate(48_000)
        handle.writeframes(b"\x00" * 3 * 48_000)

    with pytest.raises(ValueError, match="ffmpeg"):
        list(WavFileSource(caminho).blocks())
