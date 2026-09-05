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


# --------------------------------------------------------- vozes e nomeação

VTT = """WEBVTT

00:00:04.000 --> 00:00:09.500
<v Ana Souza>Bom dia, vamos ao cronograma.</v>

00:00:12.000 --> 00:00:17.000
<v Carlos Pinto>O prazo dos transceivers me preocupa.</v>
"""


def gravar_diarizado(tmp_path, gerar_audio, escrever_wav, monkeypatch):
    """Grava um WAV com duas vozes sintéticas e devolve o .json resultante."""
    fala, silencio = gerar_audio
    caminho = escrever_wav(
        np.concatenate([
            silencio(0.8), fala(4.7, 150), silencio(1.4), fala(4.0, 300), silencio(0.8),
        ]),
        "reuniao.wav",
    )
    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(tmp_path / "saida"))
    monkeypatch.setenv("ESCRIBA_DIARIZACAO", "1")
    monkeypatch.setenv("ESCRIBA_DIARIZACAO_ENGINE", "mock")
    assert main(["--motor", "mock", "arquivo", str(caminho)]) == 0
    return next((tmp_path / "saida").glob("*.json"))


def test_arquivo_com_diarizacao_separa_as_vozes(tmp_path, gerar_audio, escrever_wav, monkeypatch):
    dados = json.loads(
        gravar_diarizado(tmp_path, gerar_audio, escrever_wav, monkeypatch).read_text(
            encoding="utf-8"
        )
    )
    assert len({s["speaker_id"] for s in dados["segments"]}) == 2
    assert {f["label"] for f in dados["speakers"]} == {"Falante 1", "Falante 2"}
    assert len(dados["centroids"]) == 2


def test_nomear_usa_o_transcript_oficial(
    tmp_path, gerar_audio, escrever_wav, monkeypatch, capsys
):
    transcricao = gravar_diarizado(tmp_path, gerar_audio, escrever_wav, monkeypatch)
    vtt = tmp_path / "teams.vtt"
    vtt.write_text(VTT, encoding="utf-8")

    assert main(["nomear", str(transcricao), "--transcript", str(vtt)]) == 0

    saida = capsys.readouterr().out
    assert "Ana Souza" in saida and "Carlos Pinto" in saida
    dados = json.loads(transcricao.read_text(encoding="utf-8"))
    assert {s["speaker"] for s in dados["segments"]} == {"Ana Souza", "Carlos Pinto"}


def test_nomear_simulado_nao_altera_o_arquivo(
    tmp_path, gerar_audio, escrever_wav, monkeypatch, capsys
):
    transcricao = gravar_diarizado(tmp_path, gerar_audio, escrever_wav, monkeypatch)
    antes = transcricao.read_text(encoding="utf-8")
    vtt = tmp_path / "teams.vtt"
    vtt.write_text(VTT, encoding="utf-8")

    assert main(["nomear", str(transcricao), "--transcript", str(vtt), "--simular"]) == 0
    assert "simulação" in capsys.readouterr().out
    assert transcricao.read_text(encoding="utf-8") == antes


def test_nomear_recusa_transcricao_sem_vozes(tmp_path, capsys):
    from escriba.session import Session

    session = Session("Sem diarização")
    session.add_segment(track="sistema", speaker="Participantes", start_s=0, end_s=3, text="oi")
    caminho = tmp_path / "t.json"
    caminho.write_text(session.to_json(), encoding="utf-8")
    vtt = tmp_path / "t.vtt"
    vtt.write_text(VTT, encoding="utf-8")

    assert main(["nomear", str(caminho), "--transcript", str(vtt)]) == 1
    assert "--diarizar" in capsys.readouterr().err


def test_nomear_com_cadastro_alimenta_o_banco_de_vozes(
    tmp_path, gerar_audio, escrever_wav, monkeypatch, capsys
):
    transcricao = gravar_diarizado(tmp_path, gerar_audio, escrever_wav, monkeypatch)
    vtt = tmp_path / "teams.vtt"
    vtt.write_text(VTT, encoding="utf-8")

    codigo = main(
        ["nomear", str(transcricao), "--transcript", str(vtt), "--cadastrar", "--sim"]
    )
    assert codigo == 0

    vozes = json.loads((tmp_path / "saida" / "vozes.json").read_text(encoding="utf-8"))
    nomes = {p["name"] for p in vozes["profiles"]}
    assert nomes == {"Ana Souza", "Carlos Pinto"}
    assert all(p["consent"] for p in vozes["profiles"])


def test_vozes_listar_e_remover(tmp_path, monkeypatch, capsys):
    from escriba.diarize import VoiceProfileStore

    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(tmp_path))
    VoiceProfileStore(tmp_path / "vozes.json").add("Ana Souza", np.ones(16), consent=True)

    assert main(["vozes", "listar"]) == 0
    assert "Ana Souza" in capsys.readouterr().out

    assert main(["vozes", "remover", "Ana Souza"]) == 0
    assert main(["vozes", "remover", "Ana Souza"]) == 1


def test_vozes_cadastrar_de_um_wav(tmp_path, gerar_audio, escrever_wav, monkeypatch, capsys):
    fala, silencio = gerar_audio
    caminho = escrever_wav(
        np.concatenate([silencio(0.8), fala(6.0, 180), silencio(0.8)]), "ana.wav"
    )
    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ESCRIBA_DIARIZACAO_ENGINE", "mock")

    assert main(["vozes", "cadastrar", "Ana Souza", "--audio", str(caminho), "--sim"]) == 0
    saida = capsys.readouterr().out
    assert "sensível" in saida  # o aviso da LGPD é mostrado antes de gravar
    assert "cadastrada" in saida

    vozes = json.loads((tmp_path / "vozes.json").read_text(encoding="utf-8"))
    assert vozes["profiles"][0]["name"] == "Ana Souza"


def test_cadastro_recusa_audio_sem_fala(tmp_path, gerar_audio, escrever_wav, monkeypatch, capsys):
    _, silencio = gerar_audio
    caminho = escrever_wav(silencio(4.0), "mudo.wav")
    monkeypatch.setenv("ESCRIBA_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ESCRIBA_DIARIZACAO_ENGINE", "mock")

    assert main(["vozes", "cadastrar", "Ana", "--audio", str(caminho), "--sim"]) == 1
    assert "não encontrei fala" in capsys.readouterr().err
