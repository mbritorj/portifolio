"""Fontes de áudio: microfone, loopback do sistema e arquivo WAV.

Todas entregam a mesma coisa para o pipeline — blocos ``float32`` mono na taxa
alvo — para que o resto do programa não precise saber de onde o som veio.
"""

from __future__ import annotations

import queue
import threading
import wave
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .devices import DeviceError, DeviceInfo, resolve_device
from .resample import prepare


class AudioSource(ABC):
    """Fonte de áudio que entrega blocos mono float32 em ``target_rate``."""

    name: str = "audio"

    @abstractmethod
    def blocks(self) -> Iterator[np.ndarray]:
        """Itera blocos até a fonte acabar ou ``stop()`` ser chamado."""

    def stop(self) -> None:  # noqa: B027 - gancho opcional: fontes finitas ignoram
        """Pede o encerramento. Deve poder ser chamado de outra thread."""


class SoundDeviceSource(AudioSource):
    """Captura via PortAudio (sounddevice). Serve para microfone e para monitores."""

    def __init__(
        self,
        device: DeviceInfo,
        *,
        name: str,
        target_rate: int = 16_000,
        block_ms: int = 30,
        queue_blocks: int = 64,
    ) -> None:
        self.device = device
        self.name = name
        self.target_rate = target_rate
        self.block_ms = block_ms
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=queue_blocks)
        self._stop = threading.Event()
        self._overflows = 0

    @property
    def overflows(self) -> int:
        """Blocos descartados por fila cheia (a transcrição não acompanhou o áudio)."""
        return self._overflows

    def blocks(self) -> Iterator[np.ndarray]:
        import sounddevice as sd

        capture_rate = self.device.sample_rate
        channels = min(self.device.channels, 2)
        block_size = max(1, int(capture_rate * self.block_ms / 1000))

        def callback(indata, _frames, _time, status):  # chamado pelo PortAudio
            if status:
                self._overflows += 1
            try:
                self._queue.put_nowait(indata.copy())
            except queue.Full:
                self._overflows += 1

        with sd.InputStream(
            device=self.device.index,
            channels=channels,
            samplerate=capture_rate,
            blocksize=block_size,
            dtype="float32",
            callback=callback,
        ):
            while not self._stop.is_set():
                try:
                    raw = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if raw is None:
                    break
                yield prepare(raw, capture_rate, self.target_rate)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass


class WasapiLoopbackSource(AudioSource):
    """Loopback nativo do Windows via PyAudioWPatch (WASAPI).

    Captura o que está saindo pelo dispositivo de reprodução padrão sem cabo
    virtual e sem alterar o roteamento de áudio do usuário.
    """

    def __init__(
        self,
        *,
        name: str = "sistema",
        target_rate: int = 16_000,
        block_ms: int = 30,
        device_name: str | None = None,
    ) -> None:
        self.name = name
        self.target_rate = target_rate
        self.block_ms = block_ms
        self.device_name = device_name
        self._stop = threading.Event()

    def blocks(self) -> Iterator[np.ndarray]:
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise DeviceError(
                "backend WASAPI pede o PyAudioWPatch: pip install PyAudioWPatch"
            ) from exc

        audio = pyaudio.PyAudio()
        try:
            info = self._resolve_loopback(audio, pyaudio)
            capture_rate = int(info["defaultSampleRate"])
            channels = int(info["maxInputChannels"])
            block_size = max(1, int(capture_rate * self.block_ms / 1000))
            stream = audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=capture_rate,
                frames_per_buffer=block_size,
                input=True,
                input_device_index=info["index"],
            )
            try:
                while not self._stop.is_set():
                    raw = stream.read(block_size, exception_on_overflow=False)
                    frame = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)
                    yield prepare(frame, capture_rate, self.target_rate)
            finally:
                stream.stop_stream()
                stream.close()
        finally:
            audio.terminate()

    def _resolve_loopback(self, audio, pyaudio) -> dict:
        if self.device_name:
            for info in audio.get_loopback_device_info_generator():
                if self.device_name.lower() in info["name"].lower():
                    return info
            raise DeviceError(f"loopback WASAPI {self.device_name!r} não encontrado.")
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        saida = audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for info in audio.get_loopback_device_info_generator():
            if saida["name"] in info["name"]:
                return info
        raise DeviceError(
            f"nenhum loopback WASAPI para a saída padrão ({saida['name']!r})."
        )

    def stop(self) -> None:
        self._stop.set()


class WavFileSource(AudioSource):
    """Reproduz um WAV como se fosse captura ao vivo.

    Existe por dois motivos: testar o pipeline inteiro sem hardware de áudio e
    transcrever gravações que você já tem.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "arquivo",
        target_rate: int = 16_000,
        block_ms: int = 30,
        realtime: bool = False,
    ) -> None:
        self.path = Path(path)
        self.name = name
        self.target_rate = target_rate
        self.block_ms = block_ms
        self.realtime = realtime
        self._stop = threading.Event()

    def blocks(self) -> Iterator[np.ndarray]:
        import time

        with wave.open(str(self.path), "rb") as handle:
            capture_rate = handle.getframerate()
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            if width != 2:
                raise ValueError(
                    f"{self.path}: só WAV PCM 16 bits é aceito (encontrado {width * 8} bits). "
                    "Converta com: ffmpeg -i entrada -ac 1 -ar 16000 -c:a pcm_s16le saida.wav"
                )
            block_size = max(1, int(capture_rate * self.block_ms / 1000))
            while not self._stop.is_set():
                raw = handle.readframes(block_size)
                if not raw:
                    break
                frame = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if channels > 1:
                    frame = frame.reshape(-1, channels)
                yield prepare(frame, capture_rate, self.target_rate)
                if self.realtime:
                    time.sleep(block_size / capture_rate)

    def stop(self) -> None:
        self._stop.set()


def build_source(
    kind: str,
    *,
    spec: str | int | None,
    backend: str,
    target_rate: int,
    block_ms: int,
    name: str,
) -> AudioSource:
    """Monta a fonte certa para ``kind`` ("microfone" ou "sistema")."""
    import platform

    loopback = kind == "sistema"
    if loopback and backend in ("wasapi", "auto") and platform.system() == "Windows":
        try:
            return WasapiLoopbackSource(
                name=name,
                target_rate=target_rate,
                block_ms=block_ms,
                device_name=str(spec) if spec is not None else None,
            )
        except DeviceError:
            if backend == "wasapi":
                raise
            # Em "auto", cai para o PortAudio: pode haver um cabo virtual instalado.
    device = resolve_device(spec, loopback=loopback)
    return SoundDeviceSource(device, name=name, target_rate=target_rate, block_ms=block_ms)
