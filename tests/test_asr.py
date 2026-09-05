import numpy as np
import pytest

from escriba.asr import create_transcriber
from escriba.asr.mock import MockTranscriber
from escriba.config import AsrConfig


def test_registro_devolve_o_motor_pedido():
    assert isinstance(create_transcriber(AsrConfig(engine="mock")), MockTranscriber)


def test_motor_desconhecido_lista_as_opcoes():
    with pytest.raises(ValueError, match="faster-whisper"):
        create_transcriber(AsrConfig(engine="vosk"))


def test_faster_whisper_ausente_da_instrucao_de_instalacao():
    """Sem o pacote instalado, o erro precisa dizer o que rodar."""
    motor = create_transcriber(AsrConfig(engine="faster-whisper"))
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="pip install"):
            motor.transcribe(np.zeros(1600, dtype=np.float32))


def test_mock_marca_hipotese_parcial():
    motor = MockTranscriber(AsrConfig())
    audio = np.zeros(32_000, dtype=np.float32)
    assert "parcial" in motor.transcribe(audio, partial=True).text
    assert "parcial" not in motor.transcribe(audio).text
    assert motor.chamadas == 2


def test_resultado_vazio_e_detectado():
    resultado = MockTranscriber(AsrConfig(), texto="  ").transcribe(np.zeros(160, dtype=np.float32))
    assert resultado.is_empty
