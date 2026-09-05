import numpy as np
import pytest

from escriba.audio.resample import dbfs, prepare, resample, to_mono


def test_mono_de_estereo_e_a_media_dos_canais():
    estereo = np.array([[1.0, 0.0], [0.5, 0.5], [-1.0, 1.0]], dtype=np.float32)
    assert to_mono(estereo).tolist() == pytest.approx([0.5, 0.5, 0.0])


def test_mono_de_mono_e_identidade():
    mono = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    assert to_mono(mono).tolist() == pytest.approx(mono.tolist())


def test_reamostragem_ajusta_a_duracao():
    sinal = np.zeros(48_000, dtype=np.float32)
    convertido = resample(sinal, 48_000, 16_000)
    assert convertido.size == pytest.approx(16_000, abs=32)
    assert convertido.dtype == np.float32


def test_reamostragem_preserva_a_frequencia():
    # Uma senoide de 440 Hz a 48 kHz continua sendo 440 Hz depois de virar 16 kHz.
    t = np.arange(48_000) / 48_000
    sinal = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    convertido = resample(sinal, 48_000, 16_000)

    espectro = np.abs(np.fft.rfft(convertido))
    pico_hz = np.fft.rfftfreq(convertido.size, 1 / 16_000)[int(np.argmax(espectro))]
    assert pico_hz == pytest.approx(440, abs=5)


def test_mesma_taxa_nao_altera_o_sinal():
    sinal = np.array([0.1, 0.2], dtype=np.float32)
    assert resample(sinal, 16_000, 16_000) is not None
    assert resample(sinal, 16_000, 16_000).tolist() == pytest.approx(sinal.tolist())


def test_prepare_faz_mono_e_reamostragem_de_uma_vez():
    estereo = np.zeros((44_100, 2), dtype=np.float32)
    assert prepare(estereo, 44_100, 16_000).ndim == 1


def test_dbfs_de_silencio_absoluto_e_menos_infinito():
    assert dbfs(np.zeros(100, dtype=np.float32)) == float("-inf")
    assert dbfs(np.zeros(0, dtype=np.float32)) == float("-inf")


def test_dbfs_de_escala_cheia_e_zero():
    assert dbfs(np.ones(100, dtype=np.float32)) == pytest.approx(0.0, abs=0.01)
    assert dbfs(np.full(100, 0.5, dtype=np.float32)) == pytest.approx(-6.02, abs=0.05)
