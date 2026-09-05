"""Conversão do áudio capturado para o formato que o Whisper espera: mono, 16 kHz, float32."""

from __future__ import annotations

from math import gcd

import numpy as np

try:  # scipy dá um filtro polifásico melhor; a interpolação linear é o plano B.
    from scipy.signal import resample_poly as _resample_poly
except ImportError:  # pragma: no cover - depende do ambiente
    _resample_poly = None


def to_mono(samples: np.ndarray) -> np.ndarray:
    """Mistura os canais em um só. Aceita ``(n,)`` ou ``(n, canais)``."""
    array = np.asarray(samples, dtype=np.float32)
    if array.ndim == 1:
        return array
    if array.shape[1] == 1:
        return array[:, 0].copy()
    return array.mean(axis=1, dtype=np.float32)


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Reamostra um sinal mono float32."""
    array = np.asarray(samples, dtype=np.float32)
    if source_rate == target_rate or array.size == 0:
        return array
    if _resample_poly is not None:
        divisor = gcd(int(source_rate), int(target_rate))
        converted = _resample_poly(array, target_rate // divisor, source_rate // divisor)
        return np.asarray(converted, dtype=np.float32)
    duration = array.size / source_rate
    target_size = max(1, int(round(duration * target_rate)))
    source_positions = np.arange(array.size, dtype=np.float64)
    target_positions = np.linspace(0, array.size - 1, target_size, dtype=np.float64)
    return np.interp(target_positions, source_positions, array).astype(np.float32)


def prepare(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Atalho para ``to_mono`` seguido de ``resample``."""
    return resample(to_mono(samples), source_rate, target_rate)


def dbfs(samples: np.ndarray) -> float:
    """Energia RMS do bloco em dBFS. Blocos vazios ou mudos viram -inf."""
    array = np.asarray(samples, dtype=np.float32)
    if array.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
    if rms <= 1e-10:
        return float("-inf")
    return 20.0 * np.log10(rms)
